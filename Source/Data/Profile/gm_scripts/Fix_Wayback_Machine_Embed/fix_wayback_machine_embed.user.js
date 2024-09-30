// ==UserScript==
// @name			Fix Wayback Machine Embed
// @description		Fixes object and embed tags whose source URL is missing the correct Wayback Machine modifier.
// @version			1.0.0
// @match			*://web.archive.org/web/*
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

function url_relative_to_snapshot_origin(path)
{
	// Using the example base URL (see below), the origin is "/web/20060809195900if_/http://www.garfield.com:80".
	// The following split would be ["", "web", "20060809195900if_", "http:", "", "www.garfield.com:80", "comics", "comics_archives_strip.html"]
	const base_url = new URL(document.baseURI);
	const origin = base_url.pathname.split("/").slice(0, 6).join("/");
	return new URL(origin + path, base_url);
}

// Applying this fix to applet tags seems to break them when converting relative URLs to absolute ones.
// E.g. https://web.archive.org/web/19990428181132if_/http://canvaswerks.com:80/wineandine.htm
const plugin_nodes = document.querySelectorAll("object, embed");

for(const element of plugin_nodes)
{
	let fixed = false;
	const attributes_map = new Map();

	for(const source_attribute of SOURCE_ATTRIBUTES)
	{
		attributes_map.set(source_attribute, null);
		get_object_embed_attributes(element, attributes_map);

		const source = attributes_map.get(source_attribute);
		if(source)
		{
			/*
				Before fixing the modifier, we must ensure that every URL is absolute and points to the correct file.
				Below are all five possible cases relative to a real example URL.

				Base: https://web.archive.org/web/20060809195900if_/http://www.garfield.com:80/comics/comics_archives_strip.html?1989-ga891023

				1. "https://web.archive.org/web/20060809195900if_/http://www.garfield.com:80/comics/vault_bot.swf" -> "https://web.archive.org/web/20060809195900oe_/http://www.garfield.com:80/comics/vault_bot.swf"

				2. "vault_bot.swf" -> "https://web.archive.org/web/20060809195900oe_/http://www.garfield.com:80/comics/vault_bot.swf"

				3. "https://web.archive.org/swfs/lower_vault.swf" -> "https://web.archive.org/web/20060809195900oe_/http://www.garfield.com:80/swfs/lower_vault.swf"

				4. "/swfs/lower_vault.swf" -> "https://web.archive.org/web/20060809195900oe_/http://www.garfield.com:80/swfs/lower_vault.swf"

				5."/web/20060809195900/http://www.garfield.com:80/swfs/lower_vault.swf" -> "https://web.archive.org/web/20060809195900oe_/http://www.garfield.com:80/swfs/lower_vault.swf"
			*/

			let url = null;

			try
			{
				// For examples #1.
				url = new URL(source);

				if(!url.pathname.startsWith("/web/"))
				{
					// For example #3.
					url = url_relative_to_snapshot_origin(url.pathname);
				}
			}
			catch(error)
			{
				if(source.startsWith("/") && !source.startsWith("/web/"))
				{
					// For example #4.
					url = url_relative_to_snapshot_origin(source);
				}
				else
				{
					// For example #2 and #5.
					url = new URL(source, document.baseURI);
				}
			}

			if(url.hostname === "web.archive.org")
			{
				// E.g. "https://web.archive.org/web/20000101235959/http://www.example.com" -> ["", "web", "20000101235959", "http:", "", "www.example.com"]
				const components = url.pathname.split("/");
				if(components.length >= 4)
				{
					const timestamp = components[2];
					if(components[1] === "web" && timestamp.length >= 14 && !timestamp.endsWith("oe_"))
					{
						// Examples:
						// - No Modifier: https://web.archive.org/web/20130107202832if_/http://comic.naver.com/webtoon/detail.nhn?titleId=350217&no=31&weekday=tue
						// - Wrong Modifier: https://web.archive.org/web/20000510135641if_/http://www.halloweennet.com:80/
						components[2] = timestamp.slice(0, 14) + "oe_";
						url.pathname = components.join("/");

						attributes_map.set(source_attribute, url.toString());
						set_object_embed_attributes(element, attributes_map);

						fixed = true;
					}
				}
			}
		}
	}

	if(fixed)
	{
		reload_object_embed(element);

		if(LOG) console.log("Fix Wayback Machine Embed - Fixed:", element);
	}
}