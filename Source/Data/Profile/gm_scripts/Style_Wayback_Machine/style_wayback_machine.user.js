﻿// ==UserScript==
// @name			Style Wayback Machine
// @description		Styles Wayback Machine snapshots depending on the year they were archived.
// @version			1.0.0
// @match			*://web.archive.org/web/*
// @run-at			document-start
// @resource		CSS_98 98.css
// @resource		CSS_7 7.css
// @grant			GM_getResourceURL
// ==/UserScript==

const LOG = true;

const CSS_98_URL = GM_getResourceURL("CSS_98");
const CSS_7_URL = GM_getResourceURL("CSS_7");

let stylesheet_url = CSS_7_URL;

// E.g. "https://web.archive.org/web/20000101235959if_/http://www.example.com"
// Splits into ["", "web", "20000101235959if_"]
const components = window.location.pathname.split("/", 3);
if(components.length === 3)
{
	// E.g. ["", "web", "20000101235959if_"] -> "2000"
	const year = components[2].slice(0, 4);
	if(year <= "2004")
	{
		stylesheet_url = CSS_98_URL;
	}
}

const link = document.createElement("link");
link.setAttribute("rel", "stylesheet");
link.setAttribute("href", stylesheet_url);
document.head.prepend(link);

if(LOG) console.log("Style Wayback Machine - Styled:", link);