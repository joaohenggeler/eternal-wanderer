// ==UserScript==
// @name			Replace Broken Image Icon
// @description		Replaces the icon shown when an image is missing.
// @version			1.0.0
// @grant			none
// ==/UserScript==

// The images need to be loaded before the -moz-broken pseudo-class can be used.
document.addEventListener("readystatechange", function(event)
{
	const doc = event.currentTarget;
	if(doc.readyState === "complete")
	{
		const image_nodes = doc.querySelectorAll('img:-moz-broken');
		for(const image of image_nodes)
		{
			// Pages used to tune the CSS properties:
			// - https://web.archive.org/web/20010413215619if_/http://cgi.fortune.com:80/cgi-bin/fortune/dex/dex2.cgi?Symbol=$FFX
			// - https://web.archive.org/web/20040224220629if_/http://members.tripod.com:80/~pooky1969/+nicki.html
			// - https://web.archive.org/web/19971210213651if_/http://www.acc-corp.com:80/
			// - https://web.archive.org/web/19981203150142if_/http://w3.one.net:80/~tecumsah/patch.htm
			// - https://web.archive.org/web/20020910205836if_/http://www.theozfiles.com:80/index.html

			image.style.minWidth = "24px";
			image.style.minHeight = "24px";

			let horizontal_position = "center";
			let vertical_position = "center";

			if(image.width > 24) horizontal_position = "left 6px";
			if(image.height > 24) vertical_position = "top 6px";

			image.style.objectFit = "none";
			image.style.objectPosition = horizontal_position + " " + vertical_position;

			// Don't override a visible border.
			// E.g. https://web.archive.org/web/19981203150142if_/http://w3.one.net:80/~tecumsah/patch.htm
			const border = image.getAttribute("border");
			if(!border || Number(border) === 0)
			{
				image.style.border = "1px inset gray";
				image.style.boxSizing = "border-box";
			}

			// Note that once you replace the source, the image is no longer
			// broken and -moz-broken doesn't capture it.
			image.setAttribute("src", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA4AAAAQCAIAAACp9tltAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAAEnQAABJ0Ad5mH3gAAABDSURBVChTY2ggGoCU/icEDhw4wAAEg0spAwMIYbBxmApRgaQOCHA7AFUdEFBoKrIKJDZuB2CAQaQUyCIIQEqJBQwMANJN6ccQOwjRAAAAAElFTkSuQmCC");
		}
	}
});