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
// - https://wiki.videolan.org/Documentation:WebPlugin/#Required_elements
const SOURCE_ATTRIBUTES = ["data", "src", "target", "mrl", "filename"];

function get_object_embed_attributes(element, attributes_map)
{
	if(element.tagName === "OBJECT")
	{
		for(const name of attributes_map.keys())
		{
			let value = element.getAttribute(name);
			
			if(value == null)
			{
				const param_tags = element.getElementsByTagName("param");
				for(const param of param_tags)
				{
					const param_name = param.getAttribute("name");
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
	if(element.tagName === "OBJECT")
	{
		// Convert the live collection to an array since we're going to remove
		// elements from the page while iterating.
		const param_tags = Array.from(element.getElementsByTagName("param"));
		for(const param of param_tags)
		{
			const name = param.getAttribute("name");
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

// This is a hacky way of reloading embedded videos so that any changes we make are applied correctly.
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

		const param_tags = element.getElementsByTagName("param");
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

	const object_tags = Array.from(document.getElementsByTagName("object"));
	const embed_tags = Array.from(document.getElementsByTagName("embed"));
	
	for(const element of object_tags.concat(embed_tags))
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
				if("vlcIntervalId" in vlc.dataset)
				{
					clearInterval(vlc.dataset.vlcIntervalId);
					delete vlc.dataset.vlcIntervalId;
					delete vlc.dataset.vlcLastPosition;
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
			attributes_map.set("loop", null);
			get_object_embed_attributes(element, attributes_map);			

			const loop = attributes_map.get("loop");
			if(loop == null || loop === "false" || loop === "0")
			{
				element.dataset.vlcIntervalId = setInterval(function(vlc)
				{
					// If we're right near the end of the video or if we've looped back around.
					if(Math.abs(1.0 - vlc.input.position) < 10e-3 || ("vlcLastPosition" in vlc.dataset && vlc.input.position < vlc.dataset.vlcLastPosition))
					{
						vlc.playlist.stop();
						vlc.input.position = -1;
						clearInterval(vlc.dataset.vlcIntervalId);
						delete vlc.dataset.vlcIntervalId;
						delete vlc.dataset.vlcLastPosition;
						if(LOG) console.log("Fix VLC Embed - Stopped:", vlc);
					}
					if(vlc.input.position !== -1) vlc.dataset.vlcLastPosition = vlc.input.position;
				}, 0, element);				
			}

			reload_object_embed(element);

			if(LOG) console.log("Fix VLC Embed - Fixed:", element);
		}
	}
}