// ==UserScript==
// @name			Disable Alert Confirm Prompt
// @description		Disables the alert, confirm, and prompt functions. These last two will always return false and null, respectively.
// @version			1.0.0
// @run-at			document-start
// @grant			none
// ==/UserScript==

// See: https://html.spec.whatwg.org/multipage/timers-and-user-prompts.html#dom-alert-dev
window.alert = function(message) {return;}
window.confirm = function(message) {return false;}
window.prompt = function(message, _default) {return (_default !== undefined) ? (_default) : (null);}