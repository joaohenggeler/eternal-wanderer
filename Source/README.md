# How To Set Up And Run The Eternal Wanderer
 
This page documents every relevant component of the Eternal Wanderer including how to configure and run the bot. Note that this bot is only compatible with Firefox and Windows, largely due to relying on plugins to display web media. Although the bot can be run in Windows 8.1 and Windows Server, using an updated version of Windows 10 is strongly recommended.

**Due to the reliance on obsolete plugins to play old web media, some scripts are inherently unsafe. Use this bot at your own risk.**

## Dependencies

Python 3.9 (64-bit) or later is required to run the scripts. You can install the required dependencies by running the following command:

```
pip install -r requirements.txt
```

You can also install the optional dependencies by running the following commands. These are only required if the `detect_page_language`, `tokenize_japanese_text`, `enable_text_to_speech`, or `enable_proxy` options are enabled:

```
pip install -r language_requirements.txt
pip install -r proxy_requirements.txt
```

If you want to run the scripts through a static type checker, you should also install the typing stubs used by some packages by running the following command:

```
pip install -r typing_requirements.txt
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

* [APScheduler](https://github.com/agronholm/apscheduler): to schedule the scouting, recording, and publishing scripts.

* [fastText](https://github.com/facebookresearch/fastText): to detect a page's language from its text. Only used if the `detect_page_language` option is true.

* [fugashi](https://github.com/polm/fugashi): to tokenize Japanese text retrieved from a page. Only used if the `tokenize_japanese_text` option is true.

* [comtypes](https://github.com/enthought/comtypes): to use Windows' text-to-speech API and generate page transcripts. Only used if the `enable_text_to_speech` option is true, though it should already be installed since pywinauto depends on it.

* [mitmproxy](https://github.com/mitmproxy/mitmproxy) to intercept all HTTP/HTTPS requests made by Firefox and its plugins. Used to locate missing resources in other subdomains via the CDX API while also allowing plugin media that loads slowly to finish requesting assets. Only used if the `enable_proxy` option is true.

* [tldextract](https://github.com/john-kurkowski/tldextract) to determine the correct registered domain from a URL. Only used if the `enable_proxy` option is true.

### Troubleshooting

If you encounter any errors while installing the packages, try the following two solutions before reinstalling them. Some known errors include fastText failing to install and mitmproxy not being able to create the proxy when executing `record.py`.

* Run the command `pip install --upgrade setuptools`.

* Download and install the latest [Microsoft Visual C++ Redistributable](https://docs.microsoft.com/en-US/cpp/windows/latest-supported-vc-redist?view=msvc-170).

If you followed the previous instructions and fastText still fails to install with the error `Microsoft Visual C++ 14.0 or greater is required. Get it with "Microsoft C++ Build Tools"`, try installing [this package](https://github.com/messense/fasttext-wheel) instead by running the command `pip install fasttext-wheel>=0.9.2`.

## Setup Guide

Below is a step-by-step guide on how to obtain and configure all the necessary components in order to run the bot. The [`Data` directory](Data) directory referenced below is located in the source directory.

1. Make a copy of the [`config.template.json`](config.template.json) file and rename it to `config.json`. The next steps will refer to each configuration option in this file as needed. Most of them can be left to their default values.

2. Download the portable versions of [Firefox 52 ESR](https://portableapps.com/redirect/?a=FirefoxPortableLegacy52&s=s&d=pa&f=FirefoxPortableLegacy52_52.9.0_English.paf.exe) and [Firefox 56](https://sourceforge.net/projects/portableapps/files/Mozilla%20Firefox%2C%20Portable%20Ed./Mozilla%20Firefox%2C%20Portable%20Edition%2056.0.2/FirefoxPortable_56.0.2_English.paf.exe/download) and install them in the `Data/Firefox/52.9.0` and `Data/Firefox/56.0.2`. The path to these directories is specified by the `gui_firefox_path` and `headless_firefox_path` options, respectively. Note that these options must point to `App/Firefox/firefox.exe` executable inside those two directories since the web plugins require a 32-bit version of Firefox. You may delete the 64-bit subdirectories (`App/Firefox64`) to save disk space.

3. Download [geckodriver 0.17.0](https://github.com/mozilla/geckodriver/releases/download/v0.17.0/geckodriver-v0.17.0-win32.zip) and [geckodriver 0.20.1](https://github.com/mozilla/geckodriver/releases/download/v0.20.1/geckodriver-v0.20.1-win32.zip) and place them in `Data/Drivers/0.17.0` and `Data/Drivers/0.20.1`. The path to these directories is specified by the `gui_webdriver_path` and `headless_webdriver_path` options, respectively. Like in the previous step, you also need the 32-bit versions of these drivers.

4. Download the [Blink Enable](https://ca-archive.us.to/storage/459/459933/blink_enable-1.1-fx.xpi) and [Greasemonkey](https://ca-archive.us.to/storage/0/748/greasemonkey-3.17-fx.xpi) Firefox extensions and place them in `Data/Extensions` as specified by the `extensions_path` option. Be sure that these extensions are enabled in the `extensions_before_running` and `extensions_after_running` options.

5. Download the necessary Firefox plugins. For most plugins, you can obtain their files from the latest [Flashpoint Core](https://bluemaxima.org/flashpoint/downloads/) release. Extract the Flashpoint archive and copy the contents of `FPSoftware/BrowserPlugins` to `Data/Plugins` as specified by the `plugins_path` option. Place the DLL files from `SoundPlayback` inside different subdirectories (e.g. `Npxgplugin.dll` in `MIDI`, `npmod32.dll` in `MOD`, `npsid.dll` in `SID`). Additionally, copy `FPSoftware\VRML\Cosmo211` to the plugins directory. As a general rule, the plugin files (`np*.dll`) must be in different directories so they can be individually toggled using the `plugins` option.

6. Download the latest 32-bit version of [VLC 3.x](https://www.videolan.org/vlc/releases/) and install it in `Data/Plugins/VLC`. Note that the web plugin was removed in VLC 4.x.

7. Download the 32-bit version of Oracle's Java 8 update 11. You can get either the [Java Development Kit (JDK)](https://download.oracle.com/otn/java/jdk/8u11-b12/jdk-8u11-windows-i586.exe) or just the [Java Runtime Environment (JRE)](https://download.oracle.com/otn/java/jdk/8u11-b12/jre-8u11-windows-i586.tar.gz), which is smaller. Install it in `Data/Plugins/Java/jdk1.8.0_11` or `Data/Plugins/Java/jre1.8.0_11` depending on the one you chose. The scripts determine the Java version by looking at this last directory's name. Note that you cannot use OpenJDK since the source code for the Java Plugin was never released before it was removed completely in Java 11.

8. Set the `use_master_plugin_registry` option to false and run the following script: `browse.py about:plugins -pluginreg`. This accomplishes two things. First, it will show you a list of every plugin installed in the previous steps. Second, it generates the `pluginreg.dat` file that will be used for future Firefox executions. The file itself is autogenerated by Firefox, but it will also be modified by the script to fix certain issues (e.g. allowing the VLC plugin to play QuickTime videos). Exit the browser by pressing enter in the console and then set the `use_master_plugin_registry` option to true. Doing so will force Firefox to use this modified file in the future.

9. Download the latest [ffmpeg](https://ffmpeg.org/download.html#build-windows) version and place the `ffmpeg.exe`, `ffprobe.exe`, and `ffplay.exe` executables in `Data/FFmpeg/bin` as specified by the `ffmpeg_path` option. It's recommended that you download the latest full GPL git master branch build. The scripts will automatically add this ffmpeg version to the PATH before running. If you already have ffmpeg in your PATH and don't want to use a different version, you can ignore this step and set `ffmpeg_path` to null.

10. Download and install the [Screen Capturer Recorder](https://github.com/rdp/screen-capture-recorder-to-video-windows-free/releases) device in order to capture the screen using ffmpeg. Note that this program requires Java. You can either use a modern Java install, or reuse the local Java install from step 7. If you choose the latter, you must add the Java executable path (e.g. `Data/Plugins/Java/jdk1.8.0_11/jre/bin` or `Data/Plugins/Java/jre1.8.0_11/bin`) to the PATH environment variable.

11. If you want to automatically detect a page's language, enable the `detect_page_language` option, download a [language identification model](https://fasttext.cc/docs/en/language-identification.html) to `Data`, and enter its path in the `language_model_path` option.

12.	If you want to generate the text-to-speech audio files, enable the `enable_text_to_speech` option and install any missing voice packages in the Windows settings by going to `Ease of Access > Speech (under Interaction) > Additional speech settings (under Related settings) > Add Voices (under Manage voices)`. Note that just installing the packages isn't enough to make the voices visible to the Microsoft Speech API. You can run the following script to generate a REG file that will automatically add all installed voices to the appropriate registry key: `voices.py -registry`. Execute the resulting `voices.reg` file and then run the following script to list every visible voice: `voices.py -list`. The script will warn you if it can't find a voice specified in the `text_to_speech_language_voices` option. The configuration template lists every language available in the Windows 10 speech menu at the time of writing. You can also use the `-speak` option together with `-list` to test each voice and make sure it works properly. Run `voices.py -list -speak all` to test all voices or `voices.py -list -speak "spanish (mexico)"` (for example) to test a specific language. If a voice test fails with the error `COMError -2147200966`, you must install that voice's language package in the Window settings by going to `Time & Language > Language > Add a language (under Preferred languages)`. You only have to install the text-to-speech feature in each package.

13. If you want to approve the recordings before publishing them to Twitter or Mastodon (i.e. enabling the `require_approval` option), you can set the portable VLC version installed in step 6 as the default MP4 file viewer.

14. To publish the recorded videos on Twitter, create an account for the bot, log into the [Twitter Developer Platform](https://developer.twitter.com/en), and apply for elevated access on the dashboard. Then, create a new project and application, set up OAuth 1.0a authentication with at least read and write permissions, and generate an access token and access token secret. Enter your application's API key, API secret, and the previous tokens into the options `twitter_api_key`, `twitter_api_secret`, `twitter_access_token`, and `twitter_access_token_secret`, respectively. At the time of writing, you need to use the standard Twitter API version 1.1 to upload videos. This requires having both elevated access and using OAuth 1.0a.

15. To publish the recorded videos on Mastodon, create an account for the bot in an appropriate instance. Choose either an instance your hosting yourself or one that was designed specifically for bots. Then, go to `Settings > Development` and create a new application. While doing so, select the `write:media` and `write:statuses` scopes and uncheck any others. Save these changes and copy the generated access token to the `mastodon_access_token` option. Finally, set the `mastodon_instance_url` option to the instance's URL.

### Additional Steps For Remote Machines

If you're hosting the bot in a remote Windows machine, there are some additional steps you may want to follow.

* It's recommended that you connect and control the machine via Virtual Network Computing (VNC) rather than Remote Desktop Protocol (RDP). When you disconnect from an RDP session, the GUI is no longer available which breaks any component that relies on interacting with it (e.g. ffmpeg when capturing the screen, pywinauto when focusing on a browser window or moving the mouse). While there are some workarounds for this using `tscon` and the `HKEY_LOCAL_MACHINE\Software\Microsoft\Terminal Server Client` registry key, using VNC seemed like the simpler and more robust option. See [this page](https://stackoverflow.com/questions/15887729/can-the-gui-of-an-rdp-session-remain-active-after-disconnect) for more details. If the `require_approval` option is enabled, you might still prefer using RDP to check the recordings since it supports audio while VNC doesn't. Make sure to connect via VNC after disconnecting from an RDP session, otherwise the bot won't be able to record the screen. Note that your remote machine must be running Windows Pro edition in order to be controlled via RDP.

* If your machine doesn't have any audio output devices, then some components will crash or show error messages during recording. These include the ffmpeg audio capture device and the MIDI web plugin. You can solve this by installing the [VB-CABLE](https://vb-audio.com/Cable/index.htm) virtual audio device and selecting the speakers as your default output device in the Windows settings by going to `Devices > Sound settings (under Related settings) > Sound Control Panel (under Related settings) > Playback`.

* It's strongly recommended that you increase the `rtbufsize` and `thread_queue_size` parameters in the `ffmpeg_recording_input_args` option to the maximum supported values for the remote machine. Check how much RAM is free while displaying a page with plugins in the browser (e.g. [this snapshot](https://web.archive.org/web/19990222174035if_/http://www.geocities.com/Heartland/Plains/1036/arranco.html)) and set `rtbufsize` somewhere close to that value. If you see the errors `real-time buffer [screen-capture-recorder] [video input] too full or near too full` or `Thread message queue blocking; consider raising the thread_queue_size option` in the recorder log file, increase `rtbufsize` and `thread_queue_size`, respectively. If these values are too low, the final recording will stutter. A good rule of thumb is setting `thread_queue_size` to 5000 and `rtbufsize` as close as possible to 2 GB while leaving around 400 MB free for Firefox, the web plugins, and the Python scripts.

* It's recommended that you adjust the scale filter dimensions in the `ffmpeg_upload_output_args` option depending on your screen capture dimensions (or on your display settings if these are set to null in `screen_capture_recorder_settings`). For example, you could change the scale filter width and height to 1920x1080 (16:9) or 1440x1080 (4:3) in order to record 1080p videos.

* Check if your machine can record pages with plugins properly while the `plugin_syncing_type` option is set to `unload`. If you notice any issues like embedded audio files not being played correctly, set this option to `reload`.

* Depending on the remote machine you're using to host the bot, it's possible that you won't be able to use the OpenGL renderer when viewing VRML worlds with the Cosmo Player. If that's the case, you should change the renderer to DirectX by setting the `cosmo_player_renderer` option to `DirectX`. The Shockwave and 3DVIA players are able to choose the best available renderer so the `shockwave_renderer` and `3dvia_renderer` options can be left to `Auto`.

* Consider disabling any appearance settings that might reduce the remote machine's performance in the Windows settings by going to `System > About > Advanced system settings (under Related settings) > Settings... (under Performance) > Visual Effects` and selecting `Adjust for best performance`. Doing this can also make the text in old pages look sharper.

* If you installed a Windows version without a product key, you should find a way to remove the activation watermark before recording (e.g. by activating Windows).

* If you want Windows to automatically sign into your account after booting then run the command `netplwiz`, uncheck `Users must enter a user name and password to use this computer` in the User Accounts window, and enter your credentials after pressing ok.

* It's recommended that you set your machine's time zone to UTC in the Windows settings by going to `Time & Language > Time zone (under Current data and time) > (UTC) Coordinated Universal Time` and pressing `Sync now`. This should make it easier to track the scheduled jobs executed by the scout, recorder, and publisher scripts.

* It's recommended that you disable any automatic Windows updates to prevent any unwanted restarts while the bot is running. You can do this in the Services settings by going to the `Windows Update` service's properties, setting the startup type to `Disabled`, and then pressing `Stop`. Note that you should only do this after setting up the bot and confirming that it works properly. This is because some of the previous steps may require the Windows Update service. For example, if you tried to install the voice packages after disabling this service, it would fail with the error `The voice package couldn't be installed`.

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

## Types Of Snapshots

The bot handles two types of snapshots: regular HTML web pages and standalone media. The first are any snapshots that were successfully archived by the Wayback Machine (i.e. a 200 status code) and whose MIME type is `text/html` or `text/plain`. The second are any other successfully archived snapshots whose MIME type does *not* match the previous criteria. In other words, any standard and non-standard audiovisual media (e.g. `audio/*`, `video/*`, `application/*`, `x-world/*`, `music/*`, etc). This allows the bot to showcase multimedia (e.g. MIDI music, QuickTime videos, VRML worlds) that was linked directly in a page instead of being embedded with the object and embed tags.

## How To Use

The Eternal Wanderer bot is normally used by running the `scout.py`, `record.py`, and `publish.py` scripts at the same time. The scout script will collect the necessary metadata in the background which doesn't generally require a lot of disk space. For the recorder script, however, continuously generating videos will eventually start taking up disk space. If you don't care about archiving the lossless recordings and you don't plan on creating a compilation of multiple snapshot videos, you can set the `keep_archive_copy` and `delete_video_after_upload` options to false and true, respectively. Much like the scout, the publisher script can also be left running in the background without any issues.

If the `require_approval` option is set to true, you must use the `approve.py` to manually watch and validate each video before it can be published. The `enqueue.py` script can be used to move specific snapshots up the queue, or to force the bot to scout/record/publish any interesting pages you find on the Wayback Machine.

## Configuration

@TODO

* `extensions_before_running`: for extensions that require restarting Firefox. Installing larger extensions before running can also reduce the time it takes to start Firefox, even if they don't require it.

* `extensions_after_running`: for extensions that can run immediately after being installed while using the browser.

## Components

@TODO

### Firefox And Selenium

@TODO

### Firefox Preferences

@TODO

### Firefox Extensions

@TODO

If you want to find more legacy Firefox extensions, download the [Classic Add-ons Archive](https://github.com/JustOff/ca-archive/releases/download/2.0.3/ca-archive-2.0.3.xpi) extension, enable it as mentioned above, and browse its catalog by running the following script: `browse.py caa: -disable_multiprocess`.

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