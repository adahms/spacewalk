package com.redhat.rhn.taskomatic.task.repomd;

import java.io.Writer;
import java.util.Iterator;

import org.apache.commons.lang.StringUtils;
import org.apache.log4j.Logger;
import org.xml.sax.SAXException;

import com.redhat.rhn.domain.channel.Channel;
import com.redhat.rhn.domain.rhnpackage.PackageEvr;
import com.redhat.rhn.frontend.dto.PackageDto;
import com.redhat.rhn.taskomatic.task.TaskConstants;

public class PrimaryXmlWriter extends RepomdWriter {

    private PackageCapabilityIterator filesIterator;
    private PackageCapabilityIterator providesIterator;
    private PackageCapabilityIterator requiresIterator;
    private PackageCapabilityIterator conflictsIterator;
    private PackageCapabilityIterator obsoletesIterator;
    private static Logger log = Logger.getLogger(PrimaryXmlWriter.class);

    public PrimaryXmlWriter(Writer writer) {
        super(writer);
    }

    public String getPrimaryXml(Channel channel) throws Exception {
        begin(channel);

        Iterator iter = getChannelPackageDtoIterator(channel);
        while (iter.hasNext()) {
            addPackage((PackageDto) iter.next());
        }

        end();

        return "";
    }

    public void end() {
        try {
            handler.endElement("metadata");
            handler.endDocument();
        } 
        catch (SAXException e) {
            throw new RepomdRuntimeException(e);
        }
    }

    public void begin(Channel channel) {
        filesIterator = new PackageCapabilityIterator(channel,
                            TaskConstants.TASK_QUERY_REPOMD_GENERATOR_CAPABILITY_FILES);
        providesIterator = new PackageCapabilityIterator(channel,
                            TaskConstants.TASK_QUERY_REPOMD_GENERATOR_CAPABILITY_PROVIDES);
        requiresIterator = new PackageCapabilityIterator(channel,
                            TaskConstants.TASK_QUERY_REPOMD_GENERATOR_CAPABILITY_REQUIRES);
        conflictsIterator = new PackageCapabilityIterator(channel,
                            TaskConstants.TASK_QUERY_REPOMD_GENERATOR_CAPABILITY_CONFLICTS);
        obsoletesIterator = new PackageCapabilityIterator(channel,
                            TaskConstants.TASK_QUERY_REPOMD_GENERATOR_CAPABILITY_OBSOLETES);
        SimpleAttributesImpl attr = new SimpleAttributesImpl();
        attr.addAttribute("xmlns", "http://linux.duke.edu/metadata/common");
        attr.addAttribute("xmlns:rpm", "http://linux.duke.edu/metadata/rpm");
        attr.addAttribute("packages", Integer.toString(channel.getPackages().size()));

        try {
            handler.startElement("metadata", attr);
        } 
        catch (SAXException e) {
            throw new RepomdRuntimeException(e);
        }
    }

    public void addPackage(PackageDto pkgDto) {
        try {
            SimpleAttributesImpl attr = new SimpleAttributesImpl();
            attr.addAttribute("type", "rpm");
            handler.startElement("package", attr);

            addBasicPackageDetails(pkgDto);
            addPackageFormatDetails(pkgDto);
            handler.endElement("package");
        } 
        catch (SAXException e) {
            throw new RepomdRuntimeException(e);
        }
    }

    private void addPackageFormatDetails(PackageDto pkgDto) throws SAXException {
        long pkgId = pkgDto.getId().longValue();

        handler.startElement("format");

        handler.addElementWithCharacters("rpm:license", sanitize(pkgId, pkgDto.getCopyright()));
        handler.addElementWithCharacters("rpm:vendor", sanitize(pkgId, pkgDto.getVendor()));
        handler.addElementWithCharacters("rpm:group", sanitize(pkgId, pkgDto.getPackageGroupName()));
        handler.addElementWithCharacters("rpm:buildhost", sanitize(pkgId, pkgDto.getBuildHost()));
        handler.addElementWithCharacters("rpm:sourcerpm", sanitize(pkgId, pkgDto.getSourceRpm()));

        SimpleAttributesImpl attr = new SimpleAttributesImpl();
        attr.addAttribute("start", pkgDto.getHeaderStart().toString());
        attr.addAttribute("end", pkgDto.getHeaderEnd().toString());
        handler.startElement("rpm:header-range", attr);
        handler.endElement("rpm:header-range");

        addPackagePrcoData(pkgDto);
        addEssentialPackageFiles(pkgId);
        handler.endElement("format");
    }

    private void addBasicPackageDetails(PackageDto pkgDto) throws SAXException {
        long pkgId = pkgDto.getId().longValue();

        handler.addElementWithCharacters("name", sanitize(pkgId, pkgDto.getPackageName()));
        handler.addElementWithCharacters("arch", sanitize(pkgId, pkgDto.getPackageArchLabel()));

        SimpleAttributesImpl attr = new SimpleAttributesImpl();
        attr.addAttribute("ver", sanitize(pkgId, pkgDto.getPackageVersion()));
        attr.addAttribute("rel", sanitize(pkgId, pkgDto.getPackageRelease()));
        attr.addAttribute("epoch", sanitize(pkgId, getPackageEpoch(pkgDto.getPackageEpoch())));
        handler.startElement("version", attr);
        handler.endElement("version");

        attr.clear();
        attr.addAttribute("type", "md5");
        attr.addAttribute("pkgid", "YES");
        handler.startElement("checksum", attr);
        handler.addCharacters(sanitize(pkgId, pkgDto.getMd5sum()));
        handler.endElement("checksum");

        handler.addElementWithCharacters("summary", sanitize(pkgId, pkgDto.getSummary()));
        handler.addElementWithCharacters("description", sanitize(pkgId, pkgDto.getDescription()));

        handler.addEmptyElement("packager");
        handler.addEmptyElement("url");

        attr.clear();
        attr.addAttribute("file", Long.toString(pkgDto.getBuildTime().getTime()/1000));
        attr.addAttribute("build", Long.toString(pkgDto.getBuildTime().getTime()/1000));
        handler.startElement("time", attr);
        handler.endElement("time");

        attr.clear();
        attr.addAttribute("package", pkgDto.getPackageSize().toString());
        attr.addAttribute("archive", pkgDto.getPayloadSize().toString());
        attr.addAttribute("installed", "");
        handler.startElement("size", attr);
        handler.endElement("size");

        String pkgFile = sanitize(pkgId, getProxyFriendlyFilename(pkgDto));

        attr.clear();
        attr.addAttribute("href", "getPackage/" + pkgFile);
        handler.startElement("location", attr);
        handler.endElement("location");
    }

    private void addPackagePrcoData(PackageDto pkgDto) throws SAXException {
        addPackageDepData(providesIterator, pkgDto.getId().longValue(), "provides");
        addPackageDepData(requiresIterator, pkgDto.getId().longValue(), "requires");
        addPackageDepData(conflictsIterator, pkgDto.getId().longValue(), "conflicts");
        addPackageDepData(obsoletesIterator, pkgDto.getId().longValue(), "obsoletes");
    }

    private void addPackageDepData(PackageCapabilityIterator pkgCapIter, long pkgId, String dep) throws SAXException {
        handler.startElement("rpm:" + dep);
        while (pkgCapIter.hasNextForPackage(pkgId)) {
            SimpleAttributesImpl attr = new SimpleAttributesImpl();
            attr.addAttribute("name", sanitize(pkgId, pkgCapIter.getString("name")));
            PackageEvr evrObj = parseEvr(sanitize(pkgId, pkgCapIter.getString("version")));

            if (evrObj.getEpoch() != null || evrObj.getVersion() != null || evrObj.getRelease() != null) {
                attr.addAttribute("flags", getSenseAsString(pkgCapIter.getNumber("sense").longValue()));
            }
            
            if (evrObj.getEpoch() != null) {
                attr.addAttribute("epoch", evrObj.getEpoch());
            } 
            else if (evrObj.getVersion() != null) {
                attr.addAttribute("epoch", "0");
            }

            if (evrObj.getVersion() != null) {
                attr.addAttribute("ver", evrObj.getVersion());
            }
            if (evrObj.getRelease() != null) {
                attr.addAttribute("rel", evrObj.getRelease());
            }

            handler.startElement("rpm:entry", attr);
            handler.endElement("rpm:entry");
        }
        handler.endElement("rpm:" + dep);
    }

    private static PackageEvr parseEvr(String evr) {
        PackageEvr evrObj = new PackageEvr();

        if (evr == null) {
            return evrObj;
        }

        String [] parts = evr.split(":");
        String vr;
        if (parts.length != 1) {
            evrObj.setEpoch(parts[0]);
            vr = parts[1];
        } 
        else {
            vr = parts[0];
        }

        int dash = vr.lastIndexOf("-");

        if (dash == -1) {
            evrObj.setVersion(vr);
        } 
        else {
            evrObj.setVersion(vr.substring(0, dash));
            evrObj.setRelease(vr.substring(dash + 1));
        }

        return evrObj;
    }

    private void addEssentialPackageFiles(long pkgId) throws SAXException {
        String regex = ".*bin/.*|^/etc/.*|^/usr/lib.sendmail$";
        while (filesIterator.hasNextForPackage(pkgId)) {
            String path = sanitize(pkgId, filesIterator.getString("name"));
            if (path.matches(regex)) {
                handler.addElementWithCharacters("file", path);
            }
        }
    }

    private String getProxyFriendlyFilename(PackageDto pkgDto) {
        String[] parts = StringUtils.split(pkgDto.getPath(), '/');
        if (parts != null && parts.length > 0) {
            return parts[parts.length - 1];
        }
        return pkgDto.getPackageName() + "-" + pkgDto.getPackageVersion() + "-"
                  + pkgDto.getPackageRelease() + "." + pkgDto.getPackageArchLabel() + ".rpm";
    }

    /**
     * @return a human readable representation of the sense
     */
    private String getSenseAsString(long senseIn) {
        long sense = senseIn & 0xf;
        if (sense == 2) {
            return "LT";
        } 
        else if (sense == 4) {
            return "GT";
        } 
        else if (sense == 8) {
            return "EQ";
        } 
        else if (sense == 10) {
            return "LE";
        } 
        else { // 12
            return "GE";
        }
    }

}
