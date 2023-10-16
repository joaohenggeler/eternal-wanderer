// ==UserScript==
// @name			Bgsound To Embed
// @description		Converts bgsound to embed tags. Avoids converting bgsound tags whose audio is already being played by an embed tag. Adapted from: https://userscripts-mirror.org/scripts/show/1827
// @version			1.0.0
// @grant			none
// ==/UserScript==

const LOG = true;

// Check if the audio is already being played using an embed tag.
// We won't bother checking for object tags since this check is
// for older pages that included both the bgsound and embed tags
// to support the most popular browsers at the time.
// E.g. https://web.archive.org/web/19970414145423if_/http://www.wthr.com:80/
const playing_tracks = [];
const embed_nodes = document.querySelectorAll("embed");

for(const embed of embed_nodes)
{
	const source = embed.getAttribute("src");
	if(source) playing_tracks.push(source);
}

const bgsound_nodes = document.querySelectorAll("bgsound");

for(const bgsound of bgsound_nodes)
{
	const source = bgsound.getAttribute("src");
	
	const already_embedded = source && playing_tracks.some(track => track.includes(source) || source.includes(track));
	if(!already_embedded)
	{
		// Examples:
		// - AU: https://web.archive.org/web/19970615064625if_/http://www.iupui.edu:80/~mtrehan/
		// - MIDI + WAV: https://web.archive.org/web/19961221002525if_/http://www.geocities.com/Heartland/8055/
		// - MIDI: https://web.archive.org/web/19961026024721if_/http://www.cnet.com:80/Content/Features/Howto/Nightmares/index.html
		// - WAV: https://web.archive.org/web/19991007033544if_/http://members.tripod.com/~wikidfreakygoose/main.html
		const embed = document.createElement("embed");

		const loop = bgsound.getAttribute("loop");
		const volume = bgsound.getAttribute("volume");

		if(source) embed.setAttribute("src", source);
		if(loop) embed.setAttribute("loop", loop);
		if(volume) embed.setAttribute("volume", volume);
		
		embed.setAttribute("autoplay", "true");
		embed.setAttribute("autostart", "true");
		embed.setAttribute("hidden", "true");
		
		// Make sure the embed element is in the document's body since some pages put the bgsound tag in the head.
		// E.g. https://web.archive.org/web/20070702203805if_/http://www.spacerock.com/htmlref/BGSOUND1.html
		document.body.append(embed);

		if(LOG) console.log("Bgsound To Embed - Converted:", embed);
	}
	else
	{
		if(LOG) console.log("Bgsound To Embed - Already Embedded:", bgsound);
	}

	bgsound.remove();
}