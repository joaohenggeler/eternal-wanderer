// ==UserScript==
// @name			Disable Alert Confirm Prompt
// @description		Disables the alert, confirm, and prompt functions. These last two will always return false and null, respectively.
// @run-at			document-start
// @version			1.0.0
// @grant			none
// ==/UserScript==

window.alert = function() {return;}
window.confirm = function() {return false;}
window.prompt = function() {return null;}