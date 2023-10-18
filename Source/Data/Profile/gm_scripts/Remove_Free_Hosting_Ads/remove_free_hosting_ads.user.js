// ==UserScript==
// @name			Remove Free Hosting Ads
// @description		Removes ads inserted by free hosting services like Angelfire and Tripod.
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

// Examples:
// - https://web.archive.org/web/20090715011436if_/http://al1ninegrandquest.angelfire.com/
// - https://web.archive.org/web/20190727172159if_/http://www.angelfire.com/ca2/scream1/scream11.html
// - https://web.archive.org/web/20231013184643if_/https://paligurl.tripod.com/index.html
if(host.endsWith("angelfire.com") || host.endsWith("tripod.com"))
{
	let ad_nodes = document.querySelectorAll("div[id='tb_container'], div[id='FooterAd']");

	for(const element of ad_nodes)
	{
		if(LOG) console.log("Remove Free Hosting Ads - Removed:", element);
		element.remove();
	}

	ad_nodes = document.querySelectorAll("div[class='adCenterClass']");

	for(const element of ad_nodes)
	{
		if(LOG) console.log("Remove Free Hosting Ads - Removed:", element.parentElement);
		element.parentElement.remove();
	}
}