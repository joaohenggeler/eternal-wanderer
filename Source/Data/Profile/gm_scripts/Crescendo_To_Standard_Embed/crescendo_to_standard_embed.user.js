﻿// ==UserScript==
// @name			Crescendo To Standard Embed
// @description		Converts non-standard Crescendo or LiveUpdate embed tags to standard ones.
// @version			1.0.0
// @grant			none
// ==/UserScript==

const LOG = true;

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

const object_and_embed_tags = document.querySelectorAll("object[type='music/crescendo'], embed[type='music/crescendo']");

for(const element of object_and_embed_tags)
{
	// E.g. <embed type="music/crescendo" song="drummer1.mid" loop="true"> to <embed type="music/crescendo" song="drummer1.mid" src="drummer1.mid" loop="true">
	// See: https://web.archive.org/web/20030812135126if_/http://www.liveupdate.com/cpauth.html
	const attributes_map = new Map();
	
	attributes_map.set("song", "");
	get_object_embed_attributes(element, attributes_map);
	const song = attributes_map.get("song");

	attributes_map.clear();
	
	attributes_map.set("src", song);
	set_object_embed_attributes(element, attributes_map);

	reload_object_embed(element);

	if(LOG) console.log("Crescendo To Standard Embed - Converted:", element);
}