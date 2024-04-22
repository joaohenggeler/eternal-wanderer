// ==UserScript==
// @name			Hide Scrollbars
// @description		Hides the scrollbars of every page element.
// @version			1.0.0
// @run-at			document-start
// @resource		CSS_HIDE_SCROLLBARS hide_scrollbars.css
// @grant			GM_getResourceURL
// ==/UserScript==

const LOG = true;

const CSS_HIDE_SCROLLBARS_URL = GM_getResourceURL("CSS_HIDE_SCROLLBARS");

const link = document.createElement("link");
link.setAttribute("rel", "stylesheet");
link.setAttribute("href", CSS_HIDE_SCROLLBARS_URL);
document.head.prepend(link);

if(LOG) console.log("Hide Scrollbars - Styled:", link);