// ==UserScript==
// @name			Remove Free Hosting Ads
// @description		Removes header and footer ads inserted by free hosting services like Angelfire and Tripod.
// @version			1.0.0
// @grant			none
// ==/UserScript==

const LOG = true;

let host = window.location.hostname;

if(host === "web.archive.org")
{
	// E.g. "https://web.archive.org/web/20000101235959if_/http://www.example.com" -> ["", "web", "20000101235959if_", "http:", "", www.example.com].
	const components = window.location.pathname.split("/", 6);
	if(components.length === 6)
	{
		host = components[5];
	}
}

if(host.endsWith("angelfire.com") || host.endsWith("tripod.com"))
{
	const ad_nodes = document.querySelectorAll("div[id='tb_container'], div[id='FooterAd'], div[id='_pa-bottom-sticky-placement']");

	for(const element of ad_nodes)
	{
		if(LOG) console.log("Remove Free Hosting Ads - Removed:", element);
		element.remove();
	}
}