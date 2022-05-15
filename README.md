# Eternal Wanderer
 
A Python bot that collects archived web page metadata from the Wayback Machine, records short videos of archived web pages, and publishes them to Twitter.

With over 682 billion web pages saved since 1996, the [Wayback Machine](https://web.archive.org/) is a great resource for exploring the early internet. One project that takes advantage of this archive is [wayback_exe](https://github.com/muffinista/wayback_exe), a bot that generates images of old web pages. The Eternal Wanderer extends this idea and records videos of these old pages so that any audiovisual media like Flash movies, Java applets, VRML worlds, and MIDI music can also be experienced.

**Due to the reliance on obsolete plugins to play old web media, the screen recorder script is inherently unsafe. Use this bot at your own risk.**

## Features

* Visits archived web pages and collects metadata from their content and from the Wayback Machine's CDX API.

* Records the screen using ffmpeg and generates short MP4 videos that show the entire page.

* Publishes the recorded videos to Twitter on a set schedule.

* Designed with now obsolete web plugins in mind. The bot will automatically perform the necessary steps so that old web media plays smoothly (e.g. Flash, Shockwave, Java, VRML, MIDI, QuickTime, etc).

* Finds and plays web media that is linked directly instead of being embedded on the page (e.g. QuickTime videos and MIDI music).

* Web pages are ranked based on specific words and embedded plugin media, and can be given different priorities so that they're scraped, recorded, or published first.

## Setup

See the [setup page](Source/Setup.md) to learn how to configure and run this bot.

## Special Thanks

* Special thanks to [muffinista](https://github.com/muffinista) for creating the wayback_exe bot.
* Special thanks to [TOMYSSHADOW](https://github.com/tomysshadow) for creating the Browser Plugin Extender and for his extensive Shockwave knowledge.
* Special thanks to [nosamu](https://github.com/n0samu) for his extensive Mozilla-based browser knowledge.