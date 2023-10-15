// ==UserScript==
// @name			Fix Vlc Embed
// @description		Fixes an issue where a video played by the VLC plugin is displayed in the wrong position on the page. Also fixes an issue where embedded media that isn't supposed to loop forever is played twice by VLC. The first fix removes the video's controls so the script allows you to toggle pause by clicking on it. Videos can also be played from the beginning by right-clicking on them.
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
	// Vetinari is the codename for VLC 3.x. Note that the web plugin was removed in VLC 4.x.
	if(typeof(element.VersionInfo) === "string" && element.VersionInfo.includes("Vetinari")) return true;

	// The previous check doesn't seem to work for the object tag
	// so we'll have to rely on the MIME type and file extension.

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

	const plugin_nodes = document.querySelectorAll("object, embed");

	for(const element of plugin_nodes)
	{
		if(object_embed_uses_vlc_plugin(element))
		{
			// Examples:
			// - AIFF: https://web.archive.org/web/20010306021445if_/http://www.big.or.jp:80/~frog/others/bbb.html
			// - AU: https://web.archive.org/web/19970615064625if_/http://www.iupui.edu:80/~mtrehan/
			// - AVI (loop = true): https://web.archive.org/web/19961223102610if_/http://www.gehenna.com:80/
			// - AVI: https://web.archive.org/web/19980221110733if_/http://heartcorps.com/journeys/voice.htm
			// - MOV (loop = False): https://web.archive.org/web/19970502031035if_/http://www.verticalonline.com/dh.html
			// - MOV: https://web.archive.org/web/20200219215301if_/http://goa103.free.fr/t_63455/media_player.php
			// - MOV: https://web.archive.org/web/20220514015040if_/https://web.nmsu.edu/~leti/portfolio/quicktimemovie.html
			// - MP3 (loop = 1): https://web.archive.org/web/20001109024100if_/http://marshall_a.tripod.com/
			// - WAV (loop = 2): https://web.archive.org/web/20140822200334if_/http://www.mountaindragon.com/html/sound1.htm
			// - WAV (loop = 8): https://web.archive.org/web/19970725113606if_/http://www.wnwcorp.com:80/pharmca/
			// - WAV (loop = INFINITE): https://web.archive.org/web/19961228051934if_/http://nyelabs.kcts.org:80/
			// - WAV: https://web.archive.org/web/19961111121936if_/http://movievan.com:80/
			// - WAV: https://web.archive.org/web/19961221002525if_/http://www.geocities.com/Heartland/8055/

			const attributes_map = new Map();
			
			// Fix an issue where the video is displayed in the wrong position on the page.
			attributes_map.set("windowless", "true");
			set_object_embed_attributes(element, attributes_map);
			
			attributes_map.clear();

			// The previous fix seems to remove the player's controls. To remedy this somewhat, we'll
			// allow the user to click on the video to toggle pause and to right-click on it to start
			// playing it from the beginning.
			//
			// See: https://wiki.videolan.org/Documentation:WebPlugin/#Playlist_object
			
			element.addEventListener("click", function(event)
			{
				const vlc = event.currentTarget;
				// Play the media if it hasn't started yet (position -1) or toggle pause if
				// it's already playing (position 0.0 to 1.0).
				if(vlc.input.position === -1) vlc.playlist.play();
				else vlc.playlist.togglePause();
			});

			element.addEventListener("contextmenu", function(event)
			{
				const vlc = event.currentTarget;
				// If the user wants to restart the media, then we no longer need to worry
				// about preventing it from looping twice (see below).
				if("vlcIntervalId" in vlc.dataset)
				{
					clearInterval(Number(vlc.dataset.vlcIntervalId));
					delete vlc.dataset.vlcLastPosition;
					delete vlc.dataset.vlcIntervalId;
				}
				vlc.playlist.stop();
				vlc.playlist.play();
			});

			// If the loop attribute is missing, VLC assumes that the media should only
			// play once (even though it plays it twice due to a bug). Note that this
			// element might contain a number in the loop attribute (e.g. when converting
			// a bgsound to an embed tag). The VLC plugin doesn't allow you to repeat
			// the media a set number of times, so we have to decide whether to play it
			// once or loop forever. We'll use the following criteria:
			//
			// Play once: no loop attribute, false, 0, 1.
			// Loop forever: true, infinite, -1, 2 or more.

			attributes_map.set("loop", null);
			get_object_embed_attributes(element, attributes_map);

			const loop = attributes_map.get("loop");
			if(["true", "infinite", "-1"].includes(loop) || Number(loop) >= 2)
			{
				attributes_map.set("loop", "true");
				set_object_embed_attributes(element, attributes_map);
			}
			else
			{
				// Stop media that isn't supposed to loop forever from being played twice by VLC.
				//
				// While there are VLC-specific events that could be used here instead, these
				// didn't seem to be called in practice.
				//
				// See:
				// - https://wiki.videolan.org/Documentation:WebPlugin/#Optional_elements
				// - https://wiki.videolan.org/Documentation:WebPlugin/#Root_object
				// - https://wiki.videolan.org/Documentation:WebPlugin/#Video_object
				attributes_map.set("loop", "false");
				set_object_embed_attributes(element, attributes_map);

				element.dataset.vlcLastPosition = "-1";
				element.dataset.vlcIntervalId = setInterval(function(vlc)
				{
					// Check if we've looped back around.
					if(vlc.input.position < Number(vlc.dataset.vlcLastPosition))
					{
						// Stopping sets the position to -1.
						vlc.playlist.stop();
						if(LOG) console.log("Fix Vlc Embed - Stopped:", vlc);
						clearInterval(Number(vlc.dataset.vlcIntervalId));
						delete vlc.dataset.vlcLastPosition;
						delete vlc.dataset.vlcIntervalId;
					}
					else
					{
						vlc.dataset.vlcLastPosition = vlc.input.position;
					}
				}, 0, element);
			}

			reload_object_embed(element);

			if(LOG) console.log("Fix Vlc Embed - Fixed:", element);
		}
	}
}