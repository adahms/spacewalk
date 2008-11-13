#!/usr/bin/env python
#
# Copyright (c) 2008 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
# 
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation. 
#
#
# Database driver for cx_Oracle
#
# As much as possible, keep functionality out of here. These classes should
# inherit from this in sql_base and generally, just wrap to catch Oracle
# specific exceptions and return generic ones. (or to deal with other Oracle
# one-offs)

import sql_base
import sql_types
import cx_Oracle
import sys
import string
import os
import types

from common import rhnException, log_debug, log_error, rhnConfig
from common import UserDictCase
from sql_base import adjust_type

ORACLE_TYPE_MAPPING = [
    (sql_types.NUMBER, cx_Oracle.NUMBER),
    (sql_types.STRING, cx_Oracle.STRING),
    (sql_types.BINARY, cx_Oracle.BINARY),
    (sql_types.LONG_BINARY, cx_Oracle.LONG_BINARY),
]


def ociTuple(names=None, row=None):
    """
    Create a ((row_name, row_value), ...) tuple for this data.
    This has the advantage of returning the entries in the order requested 
    in the select statement.
    """
    data = []
    if not names:
        raise AttributeError, "Class initialization requires a description hash" 
    if row is None:
        return ()
    for x in range(len(names)):
        name, value = __oci_name_value(names[x], row[x])
        data.append((name, value))
    return tuple(data)


def ociDict(names=None, row=None):
    """ Create a dictionary from a row description and its values. """
    data = {}
    if not names:
        raise AttributeError, "Class initialization requires a description hash"
    if row is None:
        return data
    for x in range(len(names)):
        name, value = __oci_name_value(names[x], row[x])
        data[name] = value
    return data

def __oci_name_value(names, value):
    """ Extract the name, value pair needed by ociDict and ociTuple functions. """
    # the format of the names is
    name, dbitype, dsize, dbsize, prec, scale, nullok = names
    name = name.lower()
    return name, value

class Cursor(sql_base.Cursor):
    """
    Wrapper that should just transform Oracle specific exceptions into
    something generic from sql_base.
    """
    OracleError = cx_Oracle.DatabaseError

    def __init__(self, dbh, sql=None, force=None):
        self._type_mapping = ORACLE_TYPE_MAPPING

        try:
            sql_base.Cursor.__init__(self, dbh=dbh, sql=sql,
                    force=force)
        except sql_base.SQLSchemaError, e:
            (errno, errmsg) = e.errno, e.errmsg
            if 900 <= errno <= 999:
                # Per Oracle's documentation, SQL parsing error
                args = (self.dbh, errmsg, self.sql)
                raise apply(sql_base.SQLStatementPrepareError, args)
            # XXX: we should be handling the lost connection cases
            # in here too, but we don't get that many of these and
            # besides, this is much harder to get right

            # XXX: Normally we expect the e.args to include a dump of
            # the SQL code we just passed in since we're dealing with
            # an OracleError. I hope this is always the case, of not,
            # we'll have to log the sql code here
            raise rhnException("Can not prepare statement", e.args)

    def _prepare(self, force=None):
        try:
            return sql_base.Cursor._prepare(self, force)
        except self.OracleError, e:
            raise self._build_exception(e)

    def _prepare_sql(self):
        cursor = self.dbh.cursor()

        if self.sql is not None:
            # Oracle specific extension to the Python DB API:
            cursor.prepare(self.sql)

        return cursor

    def _execute_wrapper(self, function, *p, **kw):
        params =  ','.join(["%s: %s" % (str(key), str(value)) for key, value \
                in kw.items()])
        log_debug(5, "Executing SQL: \"%s\" with bind params: {%s}"
                % (self.sql, params))
        if self.sql is None:
            raise rhnException("Cannot execute empty cursor")
        modified_params = self._munge_args(kw)

        try:
            retval = apply(function, p, kw)
        except self.OracleError, e:
            ret = self._get_oracle_error_info(e)
            if isinstance(ret, types.StringType):
                raise sql_base.SQLError(self.sql, p, kw, ret)
            (errno, errmsg) = ret[:2]
            if 900 <= errno <= 999:
                # Per Oracle's documentation, SQL parsing error
                args = (errno, errmsg, self.sql)
                raise apply(sql_base.SQLStatementPrepareError, args)
            if errno == 1475: # statement needs to be reparsed; force a prepare again
                if self.reparsed: # useless, tried that already. give up
                    log_error("Reparsing cursor did not fix it", self.sql)
                    args = ("Reparsing tried and still got this",) + tuple(ret)
                    raise apply(sql_base.SQLError, args)
                self._real_cursor = self.dbh.prepare(self.sql)
                self.reparsed = 1
                apply(self._execute_wrapper, (function, ) + p, kw)
            elif 20000 <= errno <= 20999: # error codes we know we raise as schema errors
                raise apply(sql_base.SQLSchemaError, ret)
            raise apply(sql_base.SQLError, ret)
        else:
            self.reparsed = 0 # reset the reparsed counter
        # Munge back the values
        self._unmunge_args(kw, modified_params)
        return retval

    def _execute_(self, args, kwargs):
        """
        Oracle specific execution of the query.
        """
        # TODO: args appears unused, raise exception if we see any?

        # Only copy the arguments we're interested in
        _p = UserDictCase(kwargs)
        params = {}

        # Check that all required parameters were provided:
        # NOTE: bindnames() is Oracle specific:
        for k in self._real_cursor.bindnames():
            if not _p.has_key(k):
                # Raise the fault ourselves
                raise sql_base.SQLError(1008, 'Not all variables bound', k)
            params[k] = adjust_type(_p[k])

        # cx_Oracle expects the first arg to be the statement and no
        # positional args:
        self._real_cursor.execute(*(None, ), **params)
        self.description = self._real_cursor.description
        return self._real_cursor.rowcount

    def _executemany(self, *args, **kwargs):
        """
        Execute query multiple times.

        kwargs provided as positional lists.
        i.e. executemany(arg1=['a', 'b'], arg2=[1, 2])
        """
        # cx_Oracle expects the first arg to be the statement
        if not kwargs:
            return 0
        # Compute number of values
        max_array_size = 100
        i = kwargs.itervalues()
        firstval = i.next()
        array_size = len(firstval)
        if array_size == 0:
            return 0

        chunk_size = min(max_array_size, array_size)
        pdict = {}
        for k in kwargs.iterkeys():
            pdict[k] = None
        arr = []
        for i in xrange(chunk_size):
            arr.append(pdict.copy())

        # Now arr is an array of the desired size
        rowcount = 0
        start = 0
        while start < array_size:
            item_count = min(array_size - start, chunk_size)
            # Trim the array if it is too big
            if item_count != chunk_size:
                arr = arr[:item_count]

            for i in xrange(item_count):
                pdict = arr[i]
                for k, v in kwargs.iteritems():
                    pdict[k] = adjust_type(v[start+i])
                
            # arr is now a list of dictionaries. Each dictionary contains the
            # data for one execution of the query where the key is the column
            # name and the value self explanatory.
            self._real_cursor.executemany(None, arr)
            self.description = self._real_cursor.description
                
            rowcount = rowcount + self._real_cursor.rowcount
            start = start + chunk_size

        return rowcount

    def execute_bulk(self, dict, chunk_size=100):
        """
        When attempting to execute bulk operations with a lot of rows in the
        arrays,
        Oracle may occasionally lock (probably the oracle client library).
        I noticed this previously with the import code. -- misa
        This function executes bulk operations in smaller chunks
        dict is supposed to be the dictionary that we normally apply to
        statement.execute.
        """

        ret = 0
        start_chunk = 0
        while 1:
            subdict = {}
            for k, arr in dict.items():
                subarr = arr[start_chunk:start_chunk + chunk_size]
                if not subarr:
                    # Nothing more to do here - we exhausted the array(s)
                    return ret
                subdict[k] = subarr
            ret = ret + apply(self.executemany, (), subdict)
            start_chunk = start_chunk + chunk_size

        # Should never reach this point
        return ret

    def fetchone_dict(self):
        ret = ociDict(self.description, self._real_cursor.fetchone())

        if len(ret) == 0:
            return None
        return ret

    def fetchmany_dict(self, howmany=None):
        rows = self._real_cursor.fetchmany(howmany)

        ret = []
        for x in rows:
            d = ociDict(self.description, x)
            if len(d) > 0:
                ret.append(d)
        if ret == []:
            return None
        return ret

    def fetchall_dict(self):
        rows = self._real_cursor.fetchall()

        ret = []
        for x in rows:
            d = ociDict(self.description, x)
            if len(d) > 0:
                ret.append(d)
        if ret == []:
            return None
        return ret

    def fetchone_tuple(self):
        ret = ociTuple(self.description, 
            self._real_cursor.fetchone())

        if len(ret) == 0:
            return None
        return ret

    def fetchmany_tuple(self, howmany=None):
        rows = self._real_cursor.fetchmany(howmany)

        ret = []
        for x in rows:
            d = ociTuple(self.description, x)
            if len(d) > 0:
                ret.append(d)
        if ret == []:
            return None
        return ret

    def fetchall_tuple(self):
        rows = self._real_cursor.fetchall()

        ret = []
        for x in rows:
            d = ociTuple(self.description, x)
            if len(d) > 0:
                ret.append(d)
        if ret == []:
            return None
        return ret

    def _get_oracle_error_info(self, error):
        if isinstance(error, cx_Oracle.DatabaseError):
            e = error[0]
            return (e.code, e.message, self.sql)
        return str(error)

    # so we can "inherit" the self._real_cursor functions
    def __getattr__(self, name):
        if hasattr(self._real_cursor, name):
            return getattr(self._real_cursor, name)
        raise AttributeError, name

    # deletion of the object
    def __del__(self):
        self.reparsed = 0
        self.dbh = self.sql = self._real_cursor = None

    def _build_exception(self, error):
        ret = self._get_oracle_error_info(error)
        if isinstance(ret, StringType):
            return sql_base.SQLError(ret)
        return sql_base.SQLSchemaError(ret[0], ret[1])



class Procedure(sql_base.Procedure):
    OracleError = cx_Oracle.DatabaseError

    def __init__(self, name, proc):
        sql_base.Procedure.__init__(self, name, proc)
        self._type_mapping = ORACLE_TYPE_MAPPING

    def __call__(self, *args):
        """
        Wrap the __call__ method from the parent class to catch Oracle specific
        actions and convert them to something generic.
        """
        retval = None
        try:
            retval = sql_base.Procedure.__call__(self, *args)
        except cx_Oracle.DatabaseError, e:
            if not hasattr(e, "args"):
                raise sql_base.SQLError(self.name, args)
            elif 20000 <= e[0] <= 20999: # error codes we know we raise as schema errors
                raise apply(sql_base.SQLSchemaError, tuple(e.args))
            raise apply(sql_base.SQLError, tuple(e.args))
        except cx_Oracle.NotSupportedError, error:
            raise apply(sql_base.SQLError, error.args)
        return retval



class Function(Procedure):
    def __init__(self, name, proc, ret_type):
        Procedure.__init__(self, name, proc)
        self.ret_type = ret_type

    def _call_proc(self, args):
        return self._call_proc_ret(args, self.ret_type)



class Database(sql_base.Database):
    _cursor_class = Cursor
    _procedure_class = Procedure
    TimestampFromTicks = cx_Oracle.TimestampFromTicks
    OracleError = cx_Oracle.DatabaseError

    def __init__(self, host=None, port=None, username=None,
        password=None, database=None):

        # Oracle requires enough info to assembled a dsn:
        if not (username and password and database):
            raise AttributeError, "A valid Oracle username, password, and SID are required."

        sql_base.Database.__init__(self, host, port, username, password, database)
        # dbtxt is the connection string without the password
        self.dbtxt = self.dsn
        if '@' in self.dsn:
            self.dbtxt = string.split(self.dsn, '@')[-1]
        self.dbh = None

        # self.stderr keeps the sys.stderr handle alive in case it gets
        # collected too early.
        self.stderr = sys.stderr

    def connect(self, reconnect=1):
        log_debug(1, "Connecting to database", self.dbtxt)
        self._fix_environment_vars()
        try:
            self.dbh = self._connect()
        except self.OracleError, e:
            ret = self._get_oracle_error_info(e)
            if isinstance(ret, StringType):
                raise sql_base.SQLConnectError(self.dbtxt, -1,
                    "Unable to connect to database", ret)
            (errno, errmsg) = ret[:2]
            log_error("Connection attempt failed", errno, errmsg)
            if reconnect:
                # we don't try to reconnect blindly.  We have a list of
                # known "good" failure codes that warrant a reconnect
                # attempt
                if errno in [ 12547 ] : # lost contact
                    return self.connect(reconnect=0)
                err_args = [self.dbtxt, errno, errmsg]
                err_args.extend(list(ret[2:]))
                raise apply(sql_base.SQLConnectError, err_args)
            # else, this is a reconnect attempt
            raise apply(sql_base.SQLConnectError,
                [self.dbtxt, errno, errmsg,
                "Attempting Re-Connect to the database failed", ] + ret[2:])
        dbh_id = id(self.dbh)
        # Reset the statement cache for this database connection
        self._cursor_class._cursor_cache[dbh_id] = {}

    def _connect(self):
        dbh = cx_Oracle.Connection(self.dsn)
        if hasattr(sys, "argv"):
          dbh.cursor().execute(
                  "BEGIN DBMS_APPLICATION_INFO.SET_MODULE('%s',NULL); END;"
                  % sys.argv[0])
        return dbh

    # try to close it first nicely
    def close(self):
        if self.dbh is not None:
            try:
                self.dbh.close()
            except:
                pass
        log_debug(1, "Closed DB database connection to %s" % self.dbtxt)
        dbh_id = id(self.dbh)
        _cursor_cache = self._cursor_class._cursor_cache
        if _cursor_cache.has_key(dbh_id):
            _cache = _cursor_cache[dbh_id]
            for sql, cursor in _cache.items():
                # Close cursors
                try:
                    cursor.close()
                except:
                    pass
            del _cursor_cache[dbh_id]
        self.dbh = self.dsn = None

    def cursor(self):
        return self._cursor_class(dbh=self.dbh)

    # pass-through functions for when you want to do SQL yourself
    def prepare(self, sql, force=0):
        # Abuse the map calls to get rid of SQL comments and extra spaces
        sql = string.join(filter(lambda a: len(a),
            map(string.strip,
                map(lambda a: (a + " ")[:string.find(a, '--')],
                    string.split(sql, "\n")))),
            " ")
        # this way we only hit the network once for each sql statement
        return self._cursor_class(dbh=self.dbh, sql=sql, force=force)

    def procedure(self, name):
        try:
            c = self.dbh.cursor()
        except cx_Oracle.DatabaseError, error:
            e = error[0]
            raise sql_base.SQLSchemaError(e.code, e.message, e.context)
        # Pass the cursor in so we can close it after execute()
        return self._procedure_class(name, c)

    # why would anybody need this?!
    def execute(self, sql, *args, **kwargs):
        cursor = self.prepare(sql)
        apply(cursor.execute, args, kwargs)
        return cursor

    def function(self, name, ret_type):
        if not isinstance(ret_type, sql_types.DatabaseDataType):
            raise sql_base.SQLError("Invalid return type specified", ret_type)
        return self._function(name, ret_type)

    def _function(self, name, ret_type):
        try:
            c = self.dbh.cursor()
        except cx_Oracle.DatabaseError, error:
            e = error[0]
            raise sql_base.SQLSchemaError(e.code, e.message, e.context)
        return Function(name, c, ret_type)

    # transaction support
    def transaction(self, name):
        if not name:
            raise rhnException("Can not set a transaction without a name", name)
        return self.execute("savepoint %s" % name)

    def commit(self):
        log_debug(3, self.dbtxt)
        return self.dbh.commit()

    def rollback(self, name = None):
        log_debug(3, self.dbtxt, name)
        if name: # we need to roll back to a savepoint
            return self.execute("rollback to savepoint %s" % name)
	return self.dbh.rollback()

    def check_connection(self):
        # check that this connection is valid
        try:
            h = self.prepare("select sysdate as ID from dual")
            h.execute()
        except: # try to reconnect, that one MUST WORK always
            log_error("DATABASE CONNECTION TO '%s' LOST" % self.dbtxt,
                      "Exception information: %s" % sys.exc_info()[1])
            self.connect() # only allow one try
        return 0

    # function that attempts to fix the environment variables
    def _fix_environment_vars(self):
        # Bugzilla 150452  On RHEL 4, for some reason, mod_perl tries to free
        # an invalid pointer if we set an environment variable.
        # If the environment variables are already set, this will be a noop

        if not os.environ.has_key("NLS_LANG"):
            value = None
            # Do we have a config object?
            if rhnConfig.CFG.is_initialized():
                if rhnConfig.CFG.has_key("nls_lang"):
                    # Get the value from the configuration object
                    value = rhnConfig.CFG.nls_lang
            if not value:
                # Assign a default value
                value = "english.UTF8"
            os.environ["NLS_LANG"] = value

        if not os.environ.has_key("ORACLE_HOME"):
            os.environ["ORACLE_HOME"] = os.popen("dbhome '*'").read()

    # Should return a sequence [code, message, ...] or an error message if no
    # code is to be found
    def _get_oracle_error_info(self, error):
        if isinstance(error, cx_Oracle.DatabaseError):
            e = error[0]
            return (e.code, e.message, e.context)
        return str(error)

    def _read_lob(self, lob):
        if not lob:
            return None
        return lob.read()

    def Date(self, year, month, day):
        return cx_Oracle.Date(year, month, day)

    def DatetimeFromTicks(self, ticks):
        return cx_Oracle.DatetimeFromTicks(ticks)

