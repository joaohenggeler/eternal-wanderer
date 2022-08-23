// ==UserScript==
// @name			Fix Vlc Embed
// @description		Fixes an issue where a video meant to be played by the VLC plugin would be displayed in the wrong position on the page. Also fixes an issue where embedded audio that isn't supposed to loop is played twice by VLC. The first fix removes the video's controls so the script allows you to toggle pause by clicking on it. Videos can also be played from the beginning by right-clicking on them.
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

const VLC_MIME_TYPES = new Map();
const VLC_FILE_EXTENSIONS = new Map();

function source_uses_vlc_plugin(source)
{
	let result = false;
	
	if(source)
	{
		const source_extension = source.split(".").pop();
		if(source_extension !== source) result = VLC_FILE_EXTENSIONS.has(source_extension);
	}

	return result;
}

function object_embed_uses_vlc_plugin(element)
{
	let result = false;
	const type = element.getAttribute("type");
	
	if(type) result = VLC_MIME_TYPES.has(type);

	if(!result)
	{
		for(const source_attribute of SOURCE_ATTRIBUTES)
		{
			const source = element.getAttribute(source_attribute);
			result = source_uses_vlc_plugin(source);
			if(result) break;
		}

		const param_tags = element.querySelectorAll("param");
		for(const param of param_tags)
		{
			const name = param.getAttribute("name");
			const value = param.getAttribute("value");
			if(SOURCE_ATTRIBUTES.some(source => name === source))
			{
				result = source_uses_vlc_plugin(value);
				if(result) break;
			}
		}
	}

	return result;
}

const plugins = Array.from(navigator.plugins);
const vlc_plugin = plugins.find(plugin => plugin.name.includes("VLC"));

if(vlc_plugin)
{
	for(const mime_type of Array.from(vlc_plugin))
	{
		if(mime_type.type) VLC_MIME_TYPES.set(mime_type.type, true);

		const file_extensions = mime_type.suffixes.split(",");
		for(const extension of file_extensions)
		{
			if(extension) VLC_FILE_EXTENSIONS.set(extension, true);
		}
	}

	const object_and_embed_tags = document.querySelectorAll("object, embed");

	for(const element of object_and_embed_tags)
	{
		if(object_embed_uses_vlc_plugin(element))
		{
			const attributes_map = new Map();
			
			attributes_map.set("windowless", "true");
			set_object_embed_attributes(element, attributes_map);
			
			attributes_map.clear();

			// We added the windowless attribute to fix an issue where the video wouldn't
			// be displayed in the correct position on the page. Doing this, however, seems
			// to remove the controls. To remedy this somewhat, we'll allow the user to click
			// on the audio or video to toggle pause and to right-click it to start playing
			// from the beginning.
			//
			// See: https://wiki.videolan.org/Documentation:WebPlugin/#Playlist_object
			//
			// Examples:
			// - AVI: https://web.archive.org/web/19980221110733if_/http://heartcorps.com/journeys/voice.htm
			// - MOV: https://web.archive.org/web/19970502031035if_/http://www.verticalonline.com/dh.html
			// - MOV: https://web.archive.org/web/20200219215301if_/http://goa103.free.fr/t_63455/media_player.php
			// - MOV: https://web.archive.org/web/20220514015040if_/https://web.nmsu.edu/~leti/portfolio/quicktimemovie.html
			// - WMV: https://web.archive.org/web/20200713113744if_/http://thirdplanetvideo.com/Flip4MacTestPage.html
			element.addEventListener("click", function(event)
			{
				const vlc = event.currentTarget;
				// Check if the media isn't in autoplay and it hasn't started yet (position -1).
				// Otherwise, check if the media already started (position 0.0 to 1.0).
				if(vlc.input.position === -1) vlc.playlist.play();
				else vlc.playlist.togglePause();
			});

			element.addEventListener("contextmenu", function(event)
			{
				const vlc = event.currentTarget;
				// If the user wants to restart the audio, then we no longer need to worry about
				// preventing it from looping twice (see below).
				if("intervalId" in vlc.dataset)
				{
					clearInterval(Number(vlc.dataset.intervalId));
					delete vlc.dataset.lastPosition;
					delete vlc.dataset.intervalId;
				}
				vlc.input.position = 0;
				vlc.playlist.play();
			});

			// This is a hacky way of preventing audio that isn't supposed to loop from being
			// played twice by VLC. If the loop attribute is missing, VLC assumes that the
			// audio shouldn't loop. While there are VLC-specified events that could be used
			// here instead, these didn't seem to be called in practice.
			//
			// See:
			// - https://wiki.videolan.org/Documentation:WebPlugin/#Optional_elements
			// - https://wiki.videolan.org/Documentation:WebPlugin/#Root_object
			// - https://wiki.videolan.org/Documentation:WebPlugin/#Video_object
			//
			// Examples:
			// - AIFF: https://web.archive.org/web/20010306021445if_/http://www.big.or.jp:80/~frog/others/bbb.html
			// - WAV: https://web.archive.org/web/19961221002525if_/http://www.geocities.com/Heartland/8055/
			// - WAV: https://web.archive.org/web/19990222174035if_/http://www.geocities.com/Heartland/Plains/1036/arranco.html
			attributes_map.set("loop", null);
			get_object_embed_attributes(element, attributes_map);

			const loop = attributes_map.get("loop");
			if(loop == null || loop === "false" || loop === "0")
			{
				element.dataset.lastPosition = "-1";
				element.dataset.intervalId = setInterval(function(vlc)
				{
					// Check if we've looped back around.
					if(vlc.input.position < Number(vlc.dataset.lastPosition))
					{
						vlc.playlist.stop();
						vlc.input.position = -1;
						if(LOG) console.log("Fix Vlc Embed - Stopped:", vlc);
						clearInterval(Number(vlc.dataset.intervalId));
						delete vlc.dataset.lastPosition;
						delete vlc.dataset.intervalId;
					}
					else
					{
						vlc.dataset.lastPosition = vlc.input.position;
					}
				}, 0, element);
			}

			reload_object_embed(element);

			if(LOG) console.log("Fix Vlc Embed - Fixed:", element);
		}
	}
}