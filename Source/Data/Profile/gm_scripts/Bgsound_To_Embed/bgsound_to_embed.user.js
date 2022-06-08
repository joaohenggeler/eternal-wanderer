// ==UserScript==
// @name			Bgsound To Embed
// @description		Converts bgsound to embed tags. Avoids converting bgsound tags whose audio is already being played using an embed tag. Adapted from: https://userscripts-mirror.org/scripts/show/1827
// @version			1.0.0
// @grant			none
// ==/UserScript==

const LOG = true;

// Check if the audio is already being played using an embed tag.
// We won't bother checking for object tags since this is mostly
// for older pages that included both to support the most popular
// browsers at the time.
const playing_tracks = [];
const embed_tags = document.querySelectorAll("embed");

for(const embed of embed_tags)
{
	const source = embed.getAttribute("src");
	if(source) playing_tracks.push(source);
}

const bgsound_tags = document.querySelectorAll("bgsound");

for(const bgsound of bgsound_tags)
{
	const source = bgsound.getAttribute("src");
	
	const already_embedded = source && playing_tracks.some(track => track.includes(source) || source.includes(track));
	if(!already_embedded)
	{
		const loop = bgsound.getAttribute("loop");
		const volume = bgsound.getAttribute("volume");

		const embed = document.createElement("embed");

		// Although some older browsers played the audio on loop,
		// we'll account for the worst case where someone embedded
		// a short sound effect instead of a song that was meant to
		// be looped.
		embed.setAttribute("src", (source) ? (source) : (""));
		embed.setAttribute("loop", (loop) ? (loop) : ("false"));
		embed.setAttribute("volume", (volume) ? (volume) : ("100"));
		embed.setAttribute("autoplay", "true");
		embed.setAttribute("autostart", "true");
		embed.setAttribute("hidden", "true");
		
		// Make sure the embed element is in the document's body
		// since the bgsound tag can sometimes appear in the head.
		document.body.append(embed);

		if(LOG) console.log("Bgsound To Embed - Converted:", embed);
	}
	else
	{
		if(LOG) console.log("Bgsound To Embed - Already Embedded:", bgsound);
	}

	bgsound.remove();
}