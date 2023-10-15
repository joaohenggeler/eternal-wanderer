// ==UserScript==
// @name			Disable Flash Script Access
// @description		Prevents Flash movies from executing JavaScript code or navigating to a different page.
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

const FLASH_MIME_TYPES = new Map();
const FLASH_FILE_EXTENSIONS = new Map();

function source_uses_flash_plugin(source)
{
	let result = false;
	
	if(source)
	{
		const source_extension = source.split(".").pop();
		if(source_extension !== source) result = FLASH_FILE_EXTENSIONS.has(source_extension);
	}

	return result;
}

function object_embed_uses_flash_plugin(element)
{
	let result = false;
	const type = element.getAttribute("type");
	
	if(type) result = FLASH_MIME_TYPES.has(type);

	if(!result)
	{
		for(const source_attribute of SOURCE_ATTRIBUTES)
		{
			const source = element.getAttribute(source_attribute);
			result = source_uses_flash_plugin(source);
			if(result) break;
		}

		const param_nodes = element.querySelectorAll("param");
		for(const param of param_nodes)
		{
			const name = param.getAttribute("name");
			const value = param.getAttribute("value");
			if(SOURCE_ATTRIBUTES.some(source => name === source))
			{
				result = source_uses_flash_plugin(value);
				if(result) break;
			}
		}
	}

	return result;
}

const plugins = Array.from(navigator.plugins);
const flash_plugin = plugins.find(plugin => plugin.name.includes("Flash"));

if(flash_plugin)
{
	for(const mime_type of Array.from(flash_plugin))
	{
		if(mime_type.type) FLASH_MIME_TYPES.set(mime_type.type, true);

		const file_extensions = mime_type.suffixes.split(",");
		for(const extension of file_extensions)
		{
			if(extension) FLASH_FILE_EXTENSIONS.set(extension, true);
		}
	}

	const plugin_nodes = document.querySelectorAll("object, embed");

	for(const element of plugin_nodes)
	{
		if(object_embed_uses_flash_plugin(element))
		{
			// E.g. https://web.archive.org/web/20010303084655if_/http://www.halloweennet.com/
			// See: https://helpx.adobe.com/flash/kb/control-access-scripts-host-web.html
			const attributes_map = new Map();
			attributes_map.set("allowscriptaccess", "never");
			set_object_embed_attributes(element, attributes_map);

			reload_object_embed(element);

			if(LOG) console.log("Disable Flash Script Access - Disabled:", element);
		}
	}
}