// ==UserScript==
// @name			Autoplay Random Audio And Video
// @description		Plays a random audio and video element. This includes the audio and video tags as well as any object and embed tags that use audio or video MIME types.
// @version			1.0.0
// @grant			none
// ==/UserScript==

const LOG = true;

// See:
// - https://developer.mozilla.org/en-US/docs/Web/HTML/Element/object
// - https://developer.mozilla.org/en-US/docs/Web/HTML/Element/embed
// - https://helpx.adobe.com/flash/kb/flash-object-embed-tag-attributes.html
// - https://docs.oracle.com/javase/8/docs/technotes/guides/jweb/applet/using_tags.html
// - https://wiki.videolan.org/Documentation:WebPlugin/#Required_elements
const SOURCE_ATTRIBUTES = ["data", "src", "movie", "code", "object", "target", "mrl", "filename"];

// The attribute names and values passed to and returned from the next two functions are always lowercase.

function get_object_embed_attributes(element, attributes_map)
{
	if(element.tagName === "OBJECT" || element.tagName === "APPLET")
	{
		for(const name of attributes_map.keys())
		{
			let value = element.getAttribute(name);
			
			if(!value)
			{
				const param_nodes = element.querySelectorAll("param");
				for(const param of param_nodes)
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

			if(value) value = value.toLowerCase();
			attributes_map.set(name, value);
		}
	}
	else
	{
		for(const name of attributes_map.keys())
		{
			let value = element.getAttribute(name);
			if(value) value = value.toLowerCase();
			attributes_map.set(name, value);
		}
	}
}

function set_object_embed_attributes(element, attributes_map)
{
	if(element.tagName === "OBJECT" || element.tagName === "APPLET")
	{
		const param_nodes = element.querySelectorAll("param");
		for(const param of param_nodes)
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

// Map the file extensions associated with each plugin to a MIME type.
// Used to check if the embed tag contains audio or video content when
// the media type attribute isn't specified.
const FILE_EXTENSION_TO_MIME_TYPE = new Map();
for(const mime_type of navigator.mimeTypes)
{
	if(mime_type.type)
	{
		const file_extensions = mime_type.suffixes.split(",");
		for(const extension of file_extensions)
		{
			if(extension) FILE_EXTENSION_TO_MIME_TYPE.set(extension, mime_type.type);
		}		
	}
}

function source_has_mime_type(source, mime_type_regex)
{
	let result = false;
	
	if(source)
	{
		const source_extension = source.split(".").pop();
		if(source_extension !== source)
		{
			const type = FILE_EXTENSION_TO_MIME_TYPE.get(source_extension);
			if(type && mime_type_regex.test(type))
			{
				result = true;
			}
		}
	}

	return result;
}

function object_embed_has_mime_type(element, mime_type_regex)
{
	let result = false;
	
	const type = element.getAttribute("type");	
	if(type) result = mime_type_regex.test(type);

	if(!result)
	{
		const attributes_map = new Map();
		for(const source_attribute of SOURCE_ATTRIBUTES)
		{
			attributes_map.set(source_attribute, null);
		}

		get_object_embed_attributes(element, attributes_map);

		for(const value of attributes_map.values())
		{
			result = source_has_mime_type(value, mime_type_regex);
			if(result) break;
		}
	}

	return result;
}

function is_autoplaying(element)
{
	const attributes_map = new Map();
	attributes_map.set("autoplay", null);
	attributes_map.set("autostart", null);

	get_object_embed_attributes(element, attributes_map);

	const autoplay = attributes_map.get("autoplay");
	const autostart = attributes_map.get("autostart");

	// By default, the audio and video HTML5 tags do not start playing automatically. In modern browsers,
	// the element autoplays if the attribute exists at all (even if it's an empty string). For the VLC
	// plugin (i.e. the object and embed tags), any audio and video is played automatically by default.
	const playing_by_default = ( (!autoplay && !autostart) && (element.tagName === "OBJECT" || element.tagName === "EMBED") )
							|| ( (autoplay !== null) && (element.tagName === "VIDEO" || element.tagName === "AUDIO") );

	return playing_by_default || (autoplay && autoplay !== "false" && autoplay !== "0") || (autostart && autostart !== "false" && autostart !== "0");
}

const tag_mime_types = new Map();
tag_mime_types.set("audio", new RegExp("audio/.*", "i"));
tag_mime_types.set("video", new RegExp("video/.*", "i"));

const plugin_nodes = Array.from(document.querySelectorAll("object, embed"));

// For each type of tag (audio and video), make a random element start playing if there isn't one already doing so.
// If that random element contains or is contained by an object or embed tag then these will also start playing.
for(const [tag_name, mime_type_regex] of tag_mime_types)
{
	const plugin_nodes_with_mime_type = plugin_nodes.filter(element => object_embed_has_mime_type(element, mime_type_regex));
	const regular_nodes = Array.from(document.querySelectorAll(tag_name));
	const nodes = plugin_nodes_with_mime_type.concat(regular_nodes);

	// E.g. https://web.archive.org/web/20070417172029if_/http://www.geocities.com/jerusalem4muslims/nasheed.htm
	const autoplaying = nodes.some(is_autoplaying);
	if(nodes && nodes.length && !autoplaying)
	{
		const random_index = Math.floor(Math.random() * nodes.length);
		const random_element = nodes[random_index];
		
		// An element contains itself which works for our case since we want to iterate at least once below.
		const elements_with_same_content = plugin_nodes_with_mime_type.filter(element => element.contains(random_element) || random_element.contains(element));

		// Take into account embed tags that are contained inside object tags (a common design pattern).
		// In cases like these, we can assume that both tags are meant to be playing the same content.
		for(const element of elements_with_same_content)
		{
			const attributes_map = new Map();
			attributes_map.set("autoplay", "true");
			attributes_map.set("autostart", "true");
			set_object_embed_attributes(element, attributes_map);
			
			reload_object_embed(element);

			if(LOG) console.log(`Autoplay Random Audio And Video (${nodes.length} ${tag_name} tags) - Playing:`, element);	
		}
	}
}