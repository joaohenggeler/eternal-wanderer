// ==UserScript==
// @name			Style Wayback Machine
// @description		Styles Wayback Machine snapshots depending on the year they were archived.
// @version			1.0.0
// @match			*://web.archive.org/web/*
// @resource		WIN98_CSS 98.css
// @resource		WIN7_CSS 7.css
// @grant			GM_getResourceURL
// ==/UserScript==

const LOG = true;

// Example with various form elements:
// https://web.archive.org/web/19961112095617if_/http://www.nforce.com:80/home/nfeedback.html
const WIN98_CSS_URL = GM_getResourceURL("WIN98_CSS");
const WIN7_CSS_URL = GM_getResourceURL("WIN7_CSS");

let stylesheet_url = WIN7_CSS_URL;

// E.g. "https://web.archive.org/web/20000101235959if_/http://www.example.com"
// Splits into ["", "web", "20000101235959if_"]
const components = window.location.pathname.split("/", 3);
if(components.length === 3)
{
	// E.g. ["", "web", "20000101235959if_"] -> "2000"
	const year = components[2].slice(0, 4);
	if(year < "2009")
	{
		stylesheet_url = WIN98_CSS_URL;
	}
}

const link = document.createElement("link");
link.setAttribute("rel", "stylesheet");
link.setAttribute("href", stylesheet_url);
document.head.prepend(link);

if(LOG) console.log("Style Wayback Machine - Styled:", link);