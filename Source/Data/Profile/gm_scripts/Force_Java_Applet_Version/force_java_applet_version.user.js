// ==UserScript==
// @name			Force Java Applet Version
// @description		Forces the Java Plugin to use a specific JRE version and makes every applet run in its own JVM instance.
// @version			1.0.0
// @grant			none
// ==/UserScript==

const LOG = true;

// This is used to tell the Java Plugin to use the JRE that came bundled with it instead of
// a newer installed version in the system. The applets will still work even if the versions
// don't match up, but it's useful to 
const JAVA_VERSION = "1.8.0_11";

// See:
// - https://developer.mozilla.org/en-US/docs/Web/HTML/Element/object
// - https://developer.mozilla.org/en-US/docs/Web/HTML/Element/embed
// - https://docs.oracle.com/javase/8/docs/technotes/guides/jweb/applet/using_tags.html
// - https://wiki.videolan.org/Documentation:WebPlugin/#Required_elements
const SOURCE_ATTRIBUTES = ["data", "src", "code", "object", "target", "mrl", "filename"];

// The attribute names used in get_object_embed_attributes() and set_object_embed_attributes() must be lowercase.

function get_object_embed_attributes(element, attributes_map)
{
	if(element.tagName === "OBJECT" || element.tagName === "APPLET")
	{
		for(const name of attributes_map.keys())
		{
			let value = element.getAttribute(name);
			
			if(value == null)
			{
				const param_tags = element.querySelectorAll("param");
				for(const param of param_tags)
				{
					let param_name = param.getAttribute("name");
					if(param_name) param_name = param_name.toLowerCase();
					if(param_name === name)
					{
						value = param.getAttribute("value");
						break;
					}
				}
			}

			attributes_map.set(name, value);
		}
	}
	else
	{
		for(const name of attributes_map.keys())
		{
			const value = element.getAttribute(name);
			attributes_map.set(name, value);
		}
	}
}

function set_object_embed_attributes(element, attributes_map)
{
	if(element.tagName === "OBJECT" || element.tagName === "APPLET")
	{
		const param_tags = element.querySelectorAll("param");
		for(const param of param_tags)
		{
			let name = param.getAttribute("name");
			if(name) name = name.toLowerCase();
			if(attributes_map.has(name)) param.remove();
		}

		for(const [name, value] of attributes_map)
		{
			const new_param = document.createElement("param");
			new_param.setAttribute("name", name);
			new_param.setAttribute("value", value);
			element.append(new_param);
		}
	}
	else
	{
		for(const [name, value] of attributes_map)
		{
			element.setAttribute(name, value);
		}
	}
}

// This is a hacky way of reloading embedded media so that any changes we make are applied correctly.
// See: https://stackoverflow.com/questions/86428/what-s-the-best-way-to-reload-refresh-an-iframe
function reload_object_embed(element)
{
	for(const source_attribute of SOURCE_ATTRIBUTES)
	{
		if(element.hasAttribute(source_attribute)) element[source_attribute] += "";
	}
}

const JAVA_MIME_TYPES = ["application/x-java-applet", "application/x-java-bean", "application/x-java-vm", "application/java-vm", "application/java-archive"];

function object_embed_uses_java_plugin(element)
{
	// E.g. type="application/x-java-applet;version=1.8".
	// E.g. classid="clsid:8AD9C840-044E-11D1-B3E9-00805F499D93" or classid="clsid:CAFEEFAC-xxxx-yyyy-zzzz-ABCDEFFEDCBA".
	// See: https://docs.oracle.com/javase/8/docs/technotes/guides/jweb/applet/using_tags.html
	let type = element.getAttribute("type");
	let class_id = element.getAttribute("classid");
	
	if(type) type = type.toLowerCase();
	if(class_id) class_id = class_id.toLowerCase();

	return (type && JAVA_MIME_TYPES.some(mime_type => type.startsWith(mime_type)))
		|| (class_id && class_id == "clsid:8ad9c840-044e-11d1-b3e9-00805f499d93")
		|| (class_id && class_id.startsWith("clsid:cafeefac-"));
}

/*const JAPANESE_ENCODING_JAVA_ARGUMENTS = "-Dfile.encoding=UTF8 -Duser.language=ja -Duser.country=JP";

function is_current_domain_japanese()
{
	let domain = window.location.hostname;
	const path = window.location.pathname;
	
	if(domain == "web.archive.org" && path.startsWith("/web/"))
	{
		try
		{
			// E.g. "https://web.archive.org/web/20000101235959if_/http://www.example.com" -> "http://www.example.com".
			let snapshot_url = path.split("/").slice(3).join("/");
			snapshot_url = new URL(snapshot_url);
			domain = snapshot_url.hostname;
		}
		catch(error)
		{
			// Ignore errors for invalid URLs.
		}
	}

	return domain.endsWith(".jp");
}*/

const applet_tags = Array.from(document.querySelectorAll("applet"));
let object_and_embed_tags = Array.from(document.querySelectorAll("object, embed"));
object_and_embed_tags = object_and_embed_tags.filter(object_embed_uses_java_plugin);

const java_tags = applet_tags.concat(object_and_embed_tags);
//const is_japanese_domain = is_current_domain_japanese();

for(const element of java_tags)
{
	// See: https://docs.oracle.com/javase/8/docs/technotes/guides/deploy/applet_dev_guide.html#JSDPG709
	const attributes_map = new Map();
	
	/*attributes_map.set("java-vm-args", null);
	attributes_map.set("java_arguments", null);
	get_object_embed_attributes(element, attributes_map);

	let java_arguments = attributes_map.get("java-vm-args") || attributes_map.get("java_arguments");
	if(java_arguments)
	{
		java_arguments += " " + JAPANESE_ENCODING_JAVA_ARGUMENTS;
	}
	else
	{
		java_arguments = JAPANESE_ENCODING_JAVA_ARGUMENTS;
	}

	attributes_map.clear();

	if(is_japanese_domain)
	{
		attributes_map.set("java-vm-args", java_arguments);
		attributes_map.set("java_arguments", java_arguments);
	}*/

	attributes_map.set("java_version", JAVA_VERSION);
	attributes_map.set("separate_jvm", "true");

	set_object_embed_attributes(element, attributes_map);

	reload_object_embed(element);

	if(LOG) console.log("Force Java Applet Version - Changed:", element);
}