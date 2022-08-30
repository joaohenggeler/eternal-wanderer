# How To Set Up The Eternal Wanderer
 
This page documents every relevant component of the Eternal Wanderer bot including what each Python script does, where to download third-party software, and how to configure the bot. Note that this bot is only compatible with Firefox and Windows, largely due to relying on plugins to display web media.

**Due to the reliance on obsolete plugins to play old web media, the screen recorder script is inherently unsafe. Use this bot at your own risk.**

## Dependencies

Python 3.8 or later is required to run the scripts. You can install the required dependencies by running the following command:

```
pip install -r requirements.txt
```

You can also install the optional dependencies by running the following commands. These are only required if the `detect_page_language`, `tokenize_japanese_text`, `enable_text_to_speech`, or `enable_proxy` options are enabled:

```
pip install -r language_requirements.txt
pip install -r proxy_requirements.txt
```

The following Python packages are used:

* [Selenium](https://github.com/SeleniumHQ/selenium): to visit web pages, retrieve their content, and manipulate them (e.g. scroll them during recording). Due to using older Firefox versions to display the pages, only Selenium 3.x can be used.

* [pywinauto](https://github.com/pywinauto/pywinauto): to perform automated tasks like moving the mouse or focusing on the browser window.

* [Waybackpy](https://github.com/akamhy/waybackpy): to retrieve metadata from the Wayback Machine CDX API and archive pages using the Save API.

* [requests](https://github.com/psf/requests): to check if specific Wayback Machine snapshots are available and to download binary snapshot files.

* [brotlicffi](https://github.com/python-hyper/brotlicffi): to automatically decompress Brotli-encoded requests.

* [limits](https://github.com/alisaifee/limits): to avoid making too many requests to the Wayback Machine, the CDX API, and the Save API.

* [ffmpeg-python](https://github.com/kkroening/ffmpeg-python): to record the screen and manipulate audio/video files.

* [Tweepy](https://github.com/tweepy/tweepy): to upload the recorded videos to Twitter and publish tweets.

* [Mastodon.py](https://github.com/halcy/Mastodon.py): to upload the recorded videos to Mastodon and publish toots.

* [APScheduler](https://github.com/agronholm/apscheduler): to schedule both the video recordings and uploads.

* [fastText](https://github.com/facebookresearch/fastText): to detect a page's language from its text. Only used if the `detect_page_language` option is true.

* [fugashi](https://github.com/polm/fugashi): to tokenize Japanese text retrieved from a page. Only used if the `tokenize_japanese_text` option is true.

* [comtypes](https://github.com/enthought/comtypes): to use Windows' text-to-speech API and generate page transcripts. Only used if the `enable_text_to_speech` option is true, though it should already be installed since pywinauto depends on it.

* [mitmproxy](https://github.com/mitmproxy/mitmproxy) to intercept all HTTP/HTTPS requests made by Firefox and its plugins. Used to locate missing resources in other subdomains via the CDX API while also allowing plugin media that loads slowly to finish requesting assets. Only used if the `enable_proxy` option is true.

* [tldextract](https://github.com/john-kurkowski/tldextract) to determine the correct registered domain from a URL. Only used if the `enable_proxy` option is true.

## Scripts

Below is a summary of the Python scripts located in [the source directory](Source). The first three scripts are the most important ones as they handle the metadata collection, the screen recording, and the video publishing. These were designed to either run forever or only a set number of times. Pass the `-h` command line argument to learn how to use each script.

* `scout.py`: traverses web pages archived by the Wayback Machine (snapshots) and collects metadata from their content and from the CDX API. The scout script prioritizes pages that were manually added by the user through the configuration file as well as pages whose parent snapshot contains specific words and plugin media.

* `record.py`: records the previously scouted snapshots on a set schedule by opening their pages in Firefox and scrolling through them at a set pace. If the recorder script detects that any plugins crashed or that the page was redirected while capturing the screen, the recording is aborted. **This script is inherently unsafe since it relies on web plugins (e.g. Flash, Shockwave, Java, etc).**

* `publish.py`: publishes the previously recorded snapshots to Twitter and Mastodon on a set schedule. The publisher script uploads each snapshot's MP4 video and generates a tweet with the web page's title, its date, and a link to its Wayback Machine capture.

* `approve.py`: approves snapshot recordings for publishing. This operation is optional and may only be done if the publisher script was started with the `require_approval` option set to true.

* `enqueue.py`: adds a Wayback Machine snapshot to the Eternal Wanderer queue with a given priority. This can be used to scout, record, or publish any existing or new snapshots as soon as possible.

* `compile.py`: compiles multiple snapshot recordings into a single video. This can be done for published recordings that haven't been compiled yet, or for any recordings given their database IDs. A short transition with a user-defined background color, duration, and sound effect is inserted between each recording.

* `delete.py`: deletes all video files belonging to unapproved and/or compiled recordings.

* `browse.py`: opens a URL in a Firefox version equipped with various plugins and extensions. Avoid using this version to browse live websites.

* `save.py`: saves URLs from the standard input using the Wayback Machine Save API.

* `voices.py`: lists and exports the voices used by the Microsoft Speech API.

* `stats.py`: shows snapshot and recording statistics from the database.

* `wayback_proxy_addon.py`: a mitmproxy script that tells the recorder script if the page is still making requests while also checking if any missing files are available in a different subdomain. This script should not be run directly and is instead started automatically by the recorder if the `enable_proxy` option is true.

* `common.py`: a module that defines any general purpose functions used by all scripts, including loading configuration files, connecting to the database, and interfacing with Firefox.

## Regular Use

The Eternal Wanderer bot is normally used by running the `scout.py`, `record.py`, and `publish.py` scripts at the same time. The scout script will collect the necessary metadata in the background which doesn't generally require a lot of disk space. For the recorder script, however, continuously generating videos will eventually start taking up disk space. If you don't care about archiving the lossless recordings and you don't plan on creating a compilation of multiple snapshot videos, you can set the `keep_archive_copy` and `delete_video_after_upload` options to false and true, respectively. Much like the scout, the publisher script can also be left running in the background without any issues.

If the `require_approval` option is set to true, you must use the `approve.py` to manually watch and validate each video before it can be published. The `enqueue.py` script can be used to move specific snapshots up the queue, or to force the bot to scout/record/publish any interesting pages you find on the Wayback Machine.

## Types Of Snapshots

The bot handles two types of snapshots: regular HTML web pages and standalone media. The first are any snapshots that were successfully archived by the Wayback Machine (i.e. a 200 status code) and whose MIME type is `text/html`. The second are any successfully archived snapshots whose file extension is in the `standalone_media_file_extensions` option and whose MIME type does *not* start with `text/`. In other words, any standard and non-standard audiovisual media (e.g. `audio/*`, `video/*`, `application/*`, `x-world/*`, `music/*`, etc). While regular pages are located by looking at the `href` and `src` of any tag in a page and its frames, standalone media snapshots are found by looking at the file extensions of hyperlinks in anchor tags. This allows the bot to showcase any QuickTime videos and MIDI music that were linked directly in a page (instead of being embedded with the object and embed tags).

## Guide

Below is a step-by-step guide on how to obtain and configure all the necessary components in order to run the bot. The [`Data` directory](Data) referenced below is also in the located in the source directory.

1. Make a copy of the [`config.template.json`](config.template.json) file and rename it to `config.json`. The following steps will refer to each configuration option in this file as needed. Most of them can be left to their default values.

2. Download the portable versions of [Firefox 52 ESR](https://portableapps.com/redirect/?a=FirefoxPortableLegacy52&s=s&d=pa&f=FirefoxPortableLegacy52_52.9.0_English.paf.exe) and [Firefox 56](https://sourceforge.net/projects/portableapps/files/Mozilla%20Firefox%2C%20Portable%20Ed./Mozilla%20Firefox%2C%20Portable%20Edition%2056.0.2/FirefoxPortable_56.0.2_English.paf.exe/download).

3. Install Firefox 52 ESR Portable in `Data/Firefox/52.9.0` and Firefox 56 Portable in `Data/Firefox/56.0.2`. These paths are already defined by the options `gui_firefox_path` and `headless_firefox_path`, respectively. If you use different paths be sure to change them too. Note that these options should always point to `App/Firefox/firefox.exe` inside those two directories since the web plugins require the 32-bit version of Firefox. You may delete the 64-bit subdirectory (`App/Firefox64`) to save disk space.

4. Download [geckodriver 0.17.0](https://github.com/mozilla/geckodriver/releases/download/v0.17.0/geckodriver-v0.17.0-win32.zip) and [geckodriver 0.20.1](https://github.com/mozilla/geckodriver/releases/download/v0.20.1/geckodriver-v0.20.1-win32.zip). These are used by Firefox 52 ESR and 56, respectively. Like in the previous step, you also need the 32-bit versions of these drivers.

5. Place the drivers 0.17.0 in `Data/Drivers/0.17.0` and 0.20.1 in `Data/Drivers/0.20.1`. Much like in step 3, you can use different paths as long as you change the `gui_webdriver_path` and `headless_webdriver_path` options, respectively.

6. Download the necessary Firefox extensions from the following links: [Blink Enable](https://ca-archive.us.to/storage/459/459933/blink_enable-1.1-fx.xpi), [Classic Theme Restorer](https://ca-archive.us.to/storage/472/472577/classic_theme_restorer_fx29_56-1.7.7.2-fx.xpi), and [Greasemonkey](https://ca-archive.us.to/storage/0/748/greasemonkey-3.17-fx.xpi). Place them in `Data/Extensions` as specified by the `extensions_path` option.

7. Select the extensions you want to use by toggling the filenames in the `extensions_before_running` and `extensions_after_running` options. The former is used for extensions that require restarting Firefox, while the latter is for extensions that can run immediately after being installed while using the browser. Installing larger extensions before running can also reduce the time it takes to start Firefox, even if they don't require it (e.g. Classic Theme Restorer).

8. To find more legacy Firefox extensions, download the [Classic Add-ons Archive](https://github.com/JustOff/ca-archive/releases/download/2.0.3/ca-archive-2.0.3.xpi) extension and browse its catalog by running Firefox with the `browse.py` script. Note that you cannot use this extension if you started Firefox in multiprocess mode. Be sure to set `multiprocess_firefox` to false while using this extension, and setting it back to true for regular use.

9. Download the necessary Firefox plugins. For most plugins, you can obtain their files from the latest [Flashpoint Core](https://bluemaxima.org/flashpoint/downloads/) release. Extract the Flashpoint archive and copy the contents of `FPSoftware/BrowserPlugins` to `Data/Plugins` as specified by the `plugins_path` option. Place the DLL files from `SoundPlayback` inside different subdirectories (e.g. `Npxgplugin.dll` in `MIDI`, `npmod32.dll` in `MOD`, `npsid.dll` in `SID`). As a general rule, the plugin files (`np*.dll`) must be in different directories so they can be individually toggled using the `plugins` option.

10. Download the latest 32-bit version of [VLC 3.x](https://www.videolan.org/vlc/releases/) and install it in `Data/Plugins/VLC`. Note that the web plugin was removed in VLC 4.x.

11. Download the 32-bit version of Oracle's Java 8 update 11. You can get either the [Java Development Kit (JDK)]((https://download.oracle.com/otn/java/jdk/8u11-b12/jdk-8u11-windows-i586.exe) or just the [Java Runtime Environment (JRE)](https://download.oracle.com/otn/java/jdk/8u11-b12/jre-8u11-windows-i586.tar.gz), which is smaller. Install it in `Data/Plugins/Java/jdk1.8.0_11` or `Data/Plugins/Java/jre1.8.0_11` depending on the one you chose. The scripts determine the Java version by looking at this last directory's name. Note that you cannot use OpenJDK since the source code for the Java Plugin was never released before it was removed completely in Java 11.

12. Set the `use_master_plugin_registry` option to false and run the following Python script: `browse.py about:plugins -pluginreg`. This accomplishes two things. First, it will show you a list of every plugin installed in the previous steps. Second, it generates the `pluginreg.dat` file that will be used for future Firefox executions. The file itself is autogenerated by Firefox, but it will also be modified by the script to fix certain issues (e.g. allowing the VLC plugin to play QuickTime videos). Exit the browser by pressing enter in the console and then set the `use_master_plugin_registry` option to true. Doing so will force Firefox to use this modified file in the future.

13. Download and install the [Screen Capturer Recorder](https://github.com/rdp/screen-capture-recorder-to-video-windows-free/releases) device in order to capture the screen using ffmpeg. Note that this program requires Java. You can either use a modern Java install, or reuse the local Java install from step 11. For the latter, you must add the Java executable path (e.g. `Data/Plugins/Java/jdk1.8.0_11/jre/bin` or `Data/Plugins/Java/jre1.8.0_11/bin`) to the PATH environment variable.

14. To publish the recorded videos on Twitter, create an account for the bot, log into the [Twitter Developer Platform](https://developer.twitter.com/en), and apply for elevated access on the dashboard. Then, create a new project and application, set up OAuth 1.0a authentication with at least read and write permissions, and generate an access token and access token secret. Enter your application's API key, API secret, and the previous tokens into the options `twitter_api_key`, `twitter_api_secret`, `twitter_access_token`, and `twitter_access_token_secret`, respectively. At the time of writing, you need to use the standard Twitter API version 1.1 to upload videos. This requires having both elevated access and using OAuth 1.0a.

15. To publish the recorded videos on Mastodon, create an account for the bot in an appropriate instance. Choose either an instance your hosting yourself or one that was designed specifically for bots. Then, go to Settings > Development and create a new application. While doing so, select the `write:media` and `write:statuses` scopes and uncheck any others. Save these changes and copy the generated access token to the `mastodon_access_token` option. Finally, set the `mastodon_instance_url` option to the instance's URL.

## Configuration

@TODO

## Components

@TODO

### Firefox And Selenium

@TODO

### Firefox Preferences

@TODO

### Firefox Extensions

@TODO

### Firefox Plugins

@TODO

#### Shockwave

@TODO: Browser Plugin Extender: C++ Windows XP Support for VS 2017 (v141) tools

#### Java

@TODO

Notable Java versions:

* 6u7: last version before the next generation Java Plugin.
* 7u3: last version where the security level can be LOW without crashing Firefox 52 ESR.
* 7u17: last version where the security level can be LOW.
* 7u45: last version before the exception site list was added.
* 8u11: last version where the security level can be MEDIUM.

#### Cosmo Player

@TODO

#### VLC

@TODO

### AutoIt Scripts

@TODO

## Test Cases

@TODO:

* Flash: [Adobe Flash Player About Page](https://web.archive.org/web/20220513094750/https://get.adobe.com/flashplayer/about)

* Shockwave: [Test Adobe Shockwave Player](https://web.archive.org/web/20210828174110if_/https://www.adobe.com/shockwave/welcome/index.html)

* Java + MIDI + MOD + SID: [Urbanoids](https://web.archive.org/web/20161025015506if_/http://www.javaonthebrain.com/java/noids/tpanindex.html)

* Silverlight: [Demo: IIS Smooth Streaming](https://web.archive.org/web/20220412005408if_/http://www.microsoft.com/silverlight/iis-smooth-streaming/demo/)

* Authorware: [Test Authorware Web Player](https://web.archive.org/web/20210618085926if_/https://www.adobe.com/shockwave/welcome/authorwareonly.html)

* MIDI: [YAMAHA MIDPLUG for XG Sample Gallery](https://web.archive.org/web/20021010095601if_/http://www.yamaha-xg.com/mps/index.html), [Jordan's Homepage!!!!](https://web.archive.org/web/19961221004112if_/http://www.geocities.com/TimesSquare/8497/index.html) (Crescendo)

* MIDI + WAV: [RAISINS!!!!  (really, really loud)](https://web.archive.org/web/19961221002525if_/http://www.geocities.com/Heartland/8055/)

* AIFF: [abc](https://web.archive.org/web/20010306021445if_/http://www.big.or.jp:80/~frog/others/bbb.html)

* RealAudio: [RealAudio(3K)sample](http://web.archive.org/web/19991012120206if_/http://www.big.or.jp/~frog/others/plug/hello.ra)

* QuickTime: [Leticia "Fettiplace"](https://web.archive.org/web/20220514015040if_/https://web.nmsu.edu/~leti/portfolio/quicktimemovie.html), [Testing the VLC media player Mozilla/Firefox plugin](https://web.archive.org/web/20200219215301if_/http://goa103.free.fr/t_63455/media_player.php)

* WMV: [Flip4Mac Test Page](https://web.archive.org/web/20200713113744if_/http://thirdplanetvideo.com/Flip4MacTestPage.html)

* Bgsound: [BGSOUND example](https://web.archive.org/web/20070702203805if_/http://www.spacerock.com/htmlref/BGSOUND1.html)

* New Window: [Orioles Hangout Encyclopedia](https://web.archive.org/web/20010516004218if_/http://www.geocities.com/colosseum/8533/)

* Multiple Frames + MIDI Without Embed: [Alan's Midi Paradise](https://web.archive.org/web/19961221002554if_/http://www.geocities.com:80/Hollywood/Hills/5988/)

* Redirect: [GeoCities - Heartland](https://web.archive.org/web/19990127111318if_/http://www6.geocities.com:80/Heartland/)

* Standalone Media: [puro](https://web.archive.org/web/20181106025854if_/http://www.geocities.co.jp/AnimalPark-Pochi/1130/animation.html)

* Java + MIDI + WAV+ Alert + Prompt + Multiple Frames: [XTM´s HOMEPAGE](https://web.archive.org/web/19990222174035if_/http://www.geocities.com/Heartland/Plains/1036/arranco.html)

* Java + Requires Japanese Encoding: [World Wide Adventure](https://web.archive.org/web/20210511030504if_/http://chutapita.nobody.jp/top/mapdata/zumidan1.html)