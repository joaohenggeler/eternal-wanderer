# How To Set Up And Run The Eternal Wanderer

This page documents every relevant component of the Eternal Wanderer including how to configure and run the bot. Note that this bot is only compatible with Firefox and Windows, largely due to relying on plugins to display old web media. Although the bot can be run in Windows 8.1 and Windows Server, using an updated version of Windows 10 or later is strongly recommended.

**Due to relying on obsolete plugins, some scripts are inherently unsafe. Use this bot at your own risk.**

## Dependencies

Python 3.9 (64-bit) or later is required to run the scripts. You can install the required dependencies by running the following command:

```
pip install -r requirements.txt
```

You can also install the optional dependencies by running the following commands. These are only required if the `detect_page_language`, `tokenize_japanese_text`, `enable_text_to_speech`, or `enable_proxy` configuration options are enabled:

```
pip install -r language_requirements.txt
pip install -r proxy_requirements.txt
```

If you want to run the scripts through a static type checker, you should also install the typing stubs used by some packages by running the following command:

```
pip install -r typing_requirements.txt
```

The following Python packages are used:

* [Selenium](https://github.com/SeleniumHQ/selenium): to visit web pages, scrape their content, and manipulate them (e.g. scroll them during recording). Due to running older Firefox versions to display plugin media, only Selenium 3.x can be used.

* [pywinauto](https://github.com/pywinauto/pywinauto): to perform automated tasks like moving the mouse or focusing on the browser window.

* [Waybackpy](https://github.com/akamhy/waybackpy): to retrieve metadata from the Wayback Machine CDX API and archive pages using the Save API.

* [requests](https://github.com/psf/requests): to check if specific Wayback Machine snapshots are available and to download binary snapshot files.

* [brotlicffi](https://github.com/python-hyper/brotlicffi): to automatically decompress Brotli-encoded responses.

* [limits](https://github.com/alisaifee/limits): to avoid making too many requests to the Wayback Machine, the CDX API, and the Save API.

* [tldextract](https://github.com/john-kurkowski/tldextract) to determine the correct registered domain from a URL.

* [Tweepy](https://github.com/tweepy/tweepy): to upload the recorded videos to Twitter and create posts.

* [Mastodon.py](https://github.com/halcy/Mastodon.py): to upload the recorded videos to Mastodon and create posts.

* [PyTumblr](https://github.com/tumblr/pytumblr): to upload the recorded videos to Tumblr and create posts.

* [APScheduler](https://github.com/agronholm/apscheduler): to schedule the scouting, recording, and publishing scripts.

* [fastText](https://github.com/facebookresearch/fastText): to detect a page's language from its text. Only used if `detect_page_language` is enabled.

* [fugashi](https://github.com/polm/fugashi): to tokenize Japanese text scraped from a page. Only used if `tokenize_japanese_text` is enabled.

* [comtypes](https://github.com/enthought/comtypes): to interface with Windows' text-to-speech API and generate audio recordings from a page's content. Only used if `enable_text_to_speech` is enabled, though it should already be installed since pywinauto depends on it.

* [mitmproxy](https://github.com/mitmproxy/mitmproxy) to intercept all HTTP/HTTPS requests made by Firefox and its plugins. Used to locate missing resources in other subdomains via the CDX API while also allowing plugin media that loads slowly to finish requesting assets. Only used if `enable_proxy` is enabled.

### Troubleshooting

If you encounter any errors while installing the packages, try the following two solutions before reinstalling them. Some known errors include fastText failing to install and mitmproxy not being able to create the proxy when executing `record.py`.

* Run the command `pip install --upgrade setuptools`.

* Download and install the latest [Microsoft Visual C++ Redistributable](https://docs.microsoft.com/en-US/cpp/windows/latest-supported-vc-redist?view=msvc-170).

If you followed the previous instructions and fastText still fails to install with the error `Microsoft Visual C++ 14.0 or greater is required. Get it with "Microsoft C++ Build Tools"`, try installing [this package](https://github.com/messense/fasttext-wheel) instead by running the command `pip install fasttext-wheel>=0.9.2`.

## Setup Guide

Below is a step-by-step guide on how to obtain and configure all the necessary components in order to run the bot. The [`Data` directory](Data) directory referenced below is located in the source directory.

1. Make a copy of the [`config.json.template`](config.json.template) file and rename it to `config.json`. The next steps refer to each configuration option in this file as needed. Most of them can be left to their default values.

2. Download the portable versions of [Firefox 52 ESR](https://portableapps.com/redirect/?a=FirefoxPortableLegacy52&s=s&d=pa&f=FirefoxPortableLegacy52_52.9.0_English.paf.exe) and [Firefox 56](https://sourceforge.net/projects/portableapps/files/Mozilla%20Firefox%2C%20Portable%20Ed./Mozilla%20Firefox%2C%20Portable%20Edition%2056.0.2/FirefoxPortable_56.0.2_English.paf.exe/download) and install them in the `Data/Firefox/52.9.0` and `Data/Firefox/56.0.2`. The path to these directories is specified by `gui_firefox_path` and `headless_firefox_path`  respectively. Note that these options must point to the `App/Firefox/firefox.exe` executable inside those two directories since the web plugins require a 32-bit version of Firefox. You may delete the 64-bit subdirectories (`App/Firefox64`) to save disk space.

3. Download [geckodriver 0.17.0](https://github.com/mozilla/geckodriver/releases/download/v0.17.0/geckodriver-v0.17.0-win32.zip) and [geckodriver 0.20.1](https://github.com/mozilla/geckodriver/releases/download/v0.20.1/geckodriver-v0.20.1-win32.zip) and place them in `Data/Drivers/0.17.0` and `Data/Drivers/0.20.1`. The path to these directories is specified by `gui_webdriver_path` and `headless_webdriver_path`, respectively. Like in the previous step, you also need the 32-bit versions of these drivers.

4. Download the [Blink Enable](https://ca-archive.us.to/storage/459/459933/blink_enable-1.1-fx.xpi) and [Greasemonkey](https://ca-archive.us.to/storage/0/748/greasemonkey-3.17-fx.xpi) Firefox extensions and place them in `Data/Extensions` as specified by `extensions_path`. Be sure that these extensions are enabled in `extensions_before_running` and `extensions_after_running`. If you want to find more legacy Firefox extensions, download the [Classic Add-ons Archive](https://github.com/JustOff/ca-archive/releases/download/2.0.3/ca-archive-2.0.3.xpi) extension, enable it as previously mentioned, and browse its catalog by running the following script: `browse.py caa: -disable_multiprocess`.

5. Download the necessary Firefox plugins. For most plugins, you can obtain their files from the latest [Flashpoint Core](https://bluemaxima.org/flashpoint/downloads/) release. Extract the Flashpoint archive and copy the contents of `FPSoftware/BrowserPlugins` to `Data/Plugins` as specified by `plugins_path`. Place the DLL files from `SoundPlayback` inside different subdirectories (e.g. `Npxgplugin.dll` in `MIDI`, `npmod32.dll` in `MOD`, `npsid.dll` in `SID`). Additionally, copy `FPSoftware\VRML\Cosmo211` to the plugins directory. As a general rule, the plugin files (`np*.dll`) must be in different directories so they can be individually toggled using the `plugins` option.

6. Download the latest 32-bit version of [VLC 3.x](https://www.videolan.org/vlc/releases/) and install it in `Data/Plugins/VLC`. Note that the web plugin was removed in VLC 4.x.

7. Download the 32-bit version of Oracle's Java 8 update 11. You can get either the [Java Development Kit (JDK)](https://download.oracle.com/otn/java/jdk/8u11-b12/jdk-8u11-windows-i586.exe) or just the [Java Runtime Environment (JRE)](https://download.oracle.com/otn/java/jdk/8u11-b12/jre-8u11-windows-i586.tar.gz), which is smaller. Install it in `Data/Plugins/Java/jdk1.8.0_11` or `Data/Plugins/Java/jre1.8.0_11` depending on the one you chose. The scripts determine the Java version by looking at this last directory's name. Note that you cannot use OpenJDK since the source code for the Java Plugin was never released before it was removed completely in Java 11.

8. Disable `use_master_plugin_registry` and run the following script: `browse.py about:plugins -pluginreg`. This accomplishes two things. First, it will show you a list of every plugin installed in the previous steps. Second, it generates the `pluginreg.dat` file that will be used for future Firefox executions. The file itself is autogenerated by Firefox, but it will also be modified by the script to fix certain issues (e.g. allowing the VLC plugin to play QuickTime videos). Exit the browser by pressing enter in the console and then enable `use_master_plugin_registry`. Doing so will force Firefox to use this modified file in the future.

9. Download the latest [FFmpeg](https://ffmpeg.org/download.html#build-windows) version and place the `ffmpeg.exe`, `ffprobe.exe`, and `ffplay.exe` executables in `Data/FFmpeg/bin` as specified by `ffmpeg_path`. It's recommended that you download the latest full GPL git master branch build. The scripts will automatically add this FFmpeg version to the PATH before running. If you already have FFmpeg in your PATH and don't want to use a different version, you can ignore this step and set `ffmpeg_path` to null.

10. Download the latest [FluidSynth](https://github.com/FluidSynth/fluidsynth/releases) version and place the `fluidsynth.exe` executable in `Data/FluidSynth/bin` as specified by `fluidsynth_path`. The scripts will automatically add this FluidSynth version to the PATH before running. If you already have FluidSynth in your PATH and don't want to use a different version, you can ignore this step and set `fluidsynth_path` to null. Additionally, you must place at least one SoundFont file in `sound_fonts_path` if the `media_conversion_extensions` option includes MIDI files when `enable_media_conversion` is enabled. You can download the [default Windows SoundFont](https://musical-artifacts.com/artifacts/713) or [multiple SoundFonts](https://archive.org/download/free-soundfonts-sf2-2019-04). The scripts choose one at random from `sound_fonts_path`.

11. Download and install the [Screen Capture Recorder](https://github.com/rdp/screen-capture-recorder-to-video-windows-free/releases) device in order to capture the screen using FFmpeg. Note that this program requires Java. You can either use a modern Java install or reuse the local Java install from step 7. If you choose the latter, make sure to enable `java_add_to_path` so that the Java directory path (e.g. `Data/Plugins/Java/jdk1.8.0_11/jre/bin` or `Data/Plugins/Java/jre1.8.0_11/bin`) is added to the PATH automatically.

12. If you want to automatically detect a page's language, enable `detect_page_language`, download a [language identification model](https://fasttext.cc/docs/en/language-identification.html) to `Data`, and enter its path in `language_model_path`.

13.	If you want to generate the text-to-speech audio recordings, enable `enable_text_to_speech` and install any missing voice packages in the Windows settings by going to `Ease of Access > Speech (under Interaction) > Additional speech settings (under Related settings) > Add Voices (under Manage voices)`. Note that just installing the packages isn't enough to make the voices visible to the Microsoft Speech API. You can run the following script to generate a REG file that will automatically add all installed voices to the appropriate registry key: `voices.py -registry`. Execute the resulting `voices.reg` file and then run the following script to list every visible voice: `voices.py -list`. The script will warn you if it can't find a voice specified in `text_to_speech_language_voices`. The configuration template lists every language available in the Windows 10 speech menu at the time of writing. You can also use the `-speak` argument together with `-list` to test each voice and make sure it works properly. Run `voices.py -list -speak all` to test all voices or `voices.py -list -speak "spanish (mexico)"` (for example) to test a specific language. If a voice test fails with the error `COMError -2147200966`, you must install that voice's language package in the Window settings by going to `Time & Language > Language > Add a language (under Preferred languages)`. You only have to install the text-to-speech feature in each package.

14. If you want to approve the recordings before publishing them on Twitter, Mastodon, or Tumblr (i.e. if `require_approval` is enabled), you can set the portable VLC version installed in step 6 as the default MP4 file viewer.

15. To publish the recorded videos on Twitter, create an account for the bot, log into the [Twitter Developer Platform](https://developer.twitter.com/en), and apply for elevated access on the dashboard. Then, create a new project and application, set up OAuth 1.0a authentication with at least read and write permissions, and generate an access token and access token secret. Enter your application's API key, API secret, and the previous tokens into `twitter_api_key`, `twitter_api_secret`, `twitter_access_token`, and `twitter_access_token_secret`, respectively. Alternatively, you can set these options to null and place the credentials in the `WANDERER_TWITTER_API_KEY`, `WANDERER_TWITTER_API_SECRET`, `WANDERER_TWITTER_ACCESS_TOKEN`, and `WANDERER_TWITTER_ACCESS_TOKEN_SECRET` environment variables. At the time of writing, you need to use the standard Twitter API version 1.1 to upload videos. This requires having elevated access and using OAuth 1.0a. You also need to use version 2 of the API to create tweets.

16. To publish the recorded videos on Mastodon, create an account for the bot in an appropriate instance. Choose either an instance your hosting yourself or one that was designed specifically for bots. Then, go to `Settings > Development` and create a new application. While doing so, select the `write:media` and `write:statuses` scopes and uncheck any others. Save these changes and copy the generated access token to `mastodon_access_token`. Alternatively, you can set this option to null and place the token in the `WANDERER_MASTODON_ACCESS_TOKEN` environment variable. Finally, set `mastodon_instance_url` to the instance's URL. It's strongly recommended that you enable automated post deletion on your account with a threshold of one or two weeks.

17. To publish the recorded videos on Tumblr, create an account for the bot and register a new application on [this page](https://www.tumblr.com/oauth/apps). Then, go to [this page](https://api.tumblr.com/console) and authenticate using your application's consumer key and secret. Agree to any necessary permissions, make sure `OAuth 1.0a` authentication is selected, and click on `Show keys`. Enter your application's consumer key, consumer secret, token, and token secret into `tumblr_api_key`, `tumblr_api_secret`, `tumblr_access_token`, and `tumblr_access_token_secret`, respectively. Alternatively, you can set these options to null and place the credentials in the `WANDERER_TUMBLR_API_KEY`, `WANDERER_TUMBLR_API_SECRET`, `WANDERER_TUMBLR_ACCESS_TOKEN`, and `WANDERER_TUMBLR_ACCESS_TOKEN_SECRET` environment variables.

### Additional Steps For Remote Machines

If you're hosting the bot in a remote Windows machine, there are some additional steps you may want to follow.

* It's recommended that you connect and control the machine via Virtual Network Computing (VNC) rather than Remote Desktop Protocol (RDP). When you disconnect from an RDP session, the GUI is no longer available which breaks any component that relies on interacting with it (e.g. FFmpeg when capturing the screen, pywinauto when focusing on a browser window or moving the mouse). While there are some workarounds for this using `tscon` and the `HKEY_LOCAL_MACHINE\Software\Microsoft\Terminal Server Client` registry key, using VNC seemed like the simpler and more robust choice. See [this page](https://stackoverflow.com/questions/15887729/can-the-gui-of-an-rdp-session-remain-active-after-disconnect) for more details. If `require_approval` is enabled, you might still prefer using RDP to check the recordings since it supports audio while VNC does not. Make sure to connect via VNC after disconnecting from an RDP session, otherwise the bot won't be able to record the screen. Ensure also that you run the recorder script while connected via VNC so it retrieves the correct screen resolution and DPI scaling while initializing. Note that your remote machine must be running Windows Pro edition in order to be controlled via RDP.

* If your machine doesn't have any audio output devices, then some components will crash or show error messages during recording. These include the FFmpeg audio capture device and the MIDI web plugin. You can solve this by installing the [VB-CABLE](https://vb-audio.com/Cable/index.htm) virtual audio device and selecting the speakers as your default output device in the Windows settings by going to `Devices > Sound settings (under Related settings) > Sound Control Panel (under Related settings) > Playback`.

* It's strongly recommended that you increase the `-rtbufsize` and `-thread_queue_size` parameters in `raw_ffmpeg_input_args` to the maximum supported values for the remote machine. Check how much RAM is free while displaying a page with plugins in the browser (e.g. [this snapshot](https://web.archive.org/web/19990222174035if_/http://www.geocities.com/Heartland/Plains/1036/arranco.html)) and set `-rtbufsize` somewhere close to that value. If you see the errors `real-time buffer [screen-capture-recorder] [video input] too full or near too full` or `Thread message queue blocking; consider raising the thread_queue_size option` in the recorder log file, increase `-rtbufsize` and `-thread_queue_size`, respectively. If these values are too low, the recordings will stutter. A good rule of thumb is setting `-thread_queue_size` to 5000 and `-rtbufsize` as close as possible to 2 GB while leaving around 400 to 500 MB free for Firefox, the web plugins, and the Python scripts.

* If the recordings stutter in specific situations (e.g. VRML worlds or video media files), consider lowering the frame rate from 60 to 30 FPS. You can do this by setting the `-framerate` parameter in `raw_ffmpeg_input_args` to 30, and by changing the `-r` and `-g` parameters in `upload_ffmpeg_output_args` to 30 and 15, respectively. Remember that `-g` should be half the frame rate. If `enable_media_conversion` is enabled, change the `rate` parameter in `media_conversion_ffmpeg_input_name` from `60/1` to `30/1`.

* It's recommended that you adjust the dimensions of the scale and pad filters in `upload_ffmpeg_output_args` depending on your screen capture dimensions (or on your display settings if these are set to null in `screen_capture_recorder_settings`). For example, you could change the width and height to 1920x1080 (16:9) or 1440x1080 (4:3) in order to record 1080p videos. If `enable_media_conversion` is enabled, you should also change the `size` parameter in `media_conversion_ffmpeg_input_name`.

* Check if your machine can record pages with plugins properly while `plugin_syncing_page_type` is set to `unload`. If you notice any issues like embedded audio files not being played correctly, set this option to `reload_before`. These steps also apply to `plugin_syncing_media_type`, meaning this option should stay set to `unload` unless you notice a problem while recording media file snapshots.

* Depending on the remote machine you're using to host the bot, it's possible that you won't be able to use the OpenGL renderer when viewing VRML worlds with the Cosmo Player. If that's the case, you should change the renderer to DirectX by setting `cosmo_player_renderer` to `DirectX`. The Shockwave and 3DVIA players are able to choose the best available renderer, meaning `shockwave_renderer` and `_3dvia_renderer` can be left to `Auto`.

* Consider disabling any appearance settings that might reduce the remote machine's performance in the Windows settings by going to `System > About > Advanced system settings (under Related settings) > Settings... (under Performance) > Visual Effects` and selecting `Adjust for best performance`. Doing this can also make the text in old pages look sharper.

* If you installed a Windows version without a product key, you should activate it to prevent the watermark from appearing in the recordings.

* If you want Windows to automatically sign into your account after booting then run the command `netplwiz`, uncheck `Users must enter a user name and password to use this computer` in the User Accounts window, and enter your credentials after clicking ok.

* It's recommended that you set your machine's time zone to UTC in the Windows settings by going to `Time & Language > Time zone (under Current data and time) > (UTC) Coordinated Universal Time` and clicking `Sync now`. This should make it easier to track the scheduled jobs executed by the scout, recorder, and publisher scripts.

* It's recommended that you disable any automatic Windows updates to prevent any unwanted restarts while the bot is running. You can do this in the Services settings by going to the `Windows Update` service's properties, setting the startup type to `Disabled`, and then clicking `Stop`. Note that you should only do this after setting up the bot and confirming that it works properly. This is because some of the previous steps may require the Windows Update service. For example, if you tried to install the voice packages after disabling this service, it would fail with the error `The voice package couldn't be installed`.

## Scripts

Below is a summary of the Python scripts located in the [source directory](Source). The first three scripts are the most important ones as they handle the metadata collection, the screen recording, and the video publishing. These were designed to either run forever or only a set number of times. Pass the `-h` command line argument to learn how to use each script.

* `scout.py`: traverses web pages archived by the Wayback Machine (snapshots) and collects metadata from their content and from the CDX API. The scout script prioritizes pages that were manually added by the user through the configuration file as well as pages whose parent snapshot contains specific words and plugin media.

* `record.py`: records the previously scouted snapshots on a set schedule by opening their pages in Firefox and scrolling through them at a set pace. If the recorder script detects that any plugins crashed or that the page was redirected while capturing the screen, the recording is aborted. **This script is inherently unsafe since it relies on web plugins (e.g. Flash, Shockwave, Java, etc).**

* `publish.py`: publishes the previously recorded snapshots on Twitter, Mastodon, and Tumblr on a set schedule. The publisher script uploads the recordings and generates posts with the web page's title, its date, and a link to its Wayback Machine capture.

* `approve.py`: approves recordings for publishing. This process is optional and can only be done if the publisher script was started with the `require_approval` option enabled.

* `enqueue.py`: adds a Wayback Machine snapshot to the queue with a given priority. This can be used to scout, record, or publish any existing or new snapshots as soon as possible.

* `compile.py`: compiles multiple snapshot recordings into a single video. This can be done for published recordings that haven't been compiled yet, or for any recordings given their database IDs. A short transition with a user-defined background color, duration, and sound effect is inserted between each recording.

* `delete.py`: deletes all video files belonging to unapproved and/or compiled recordings.

* `browse.py`: opens a URL in a Firefox version equipped with various plugins and extensions. Avoid using this version to browse live websites.

* `save.py`: saves URLs from the standard input using the Wayback Machine Save API.

* `voices.py`: lists and exports the voices used by the Microsoft Speech API.

* `stats.py`: shows snapshot and recording statistics from the database.

* `graph.py`: displays information based on the snapshot topology.

* `wayback_proxy_addon.py`: a mitmproxy script that tells the recorder script if the page is still making requests while also checking if any missing files are available in a different subdomain. This script should not be run directly and is instead started automatically by the recorder if `enable_proxy` is enabled.

* `dump_proxy_addon.py`: a mitmproxy script that generates a dump file containing all HTTP/HTTPS responses received by the browser. This script should not be run directly and is instead started automatically by the browser script if the `-dump` argument was used.

## Types Of Snapshots

The bot handles two types of snapshots: web pages and media files. The first are any snapshots that were successfully archived by the Wayback Machine (i.e. a 200 status code) and whose MIME type is `text/html` or `text/plain`. The second are any other successfully archived snapshots whose MIME type does *not* match the previous criteria. In other words, any standard and non-standard audiovisual media (e.g. `audio/*`, `video/*`, `application/*`, `x-world/*`, `music/*`, etc). This allows the bot to showcase multimedia (e.g. MIDI music, QuickTime videos, VRML worlds, etc) that was linked directly in a page instead of being embedded with the object and embed tags.

## Configuration

Below is a summary of what each option in the `config.json` configuration file does. This section is divided according to the three main scripts (scout, recorder, publisher) and a configuration that is common to all scripts.

### Common

Used by all scripts.

* `debug`: enable to run in debug mode, where additional information is logged.

* `locale`: the locale to be set once before running the scripts. This currently only affects how a snapshot's short date (month and year) is formatted. On Windows, this should be one of the language strings specified [here](https://learn.microsoft.com/en-us/cpp/c-runtime-library/language-strings?view=msvc-170).

* `database_path`: the path to the database file. Any missing directories are automatically created.

* `database_error_wait`: how long to wait after an unexpected database error occurs (in seconds).

* `gui_webdriver_path`: the path to the geckodriver executable used by the Firefox version specified in `gui_firefox_path`.

* `headless_webdriver_path`: the path to the geckodriver executable used by the Firefox version specified in `headless_firefox_path`.

* `page_load_timeout`: the maximum amount of time the WebDriver will wait for when loading a web page (in seconds).

* `gui_firefox_path`: the path to the Firefox executable to be run with a GUI. Used when recording snapshots. Unlike `headless_firefox_path`, this version can support web plugins.

* `headless_firefox_path`: the path to the Firefox executable to be run in headless mode (i.e. without a GUI). Used when scouting snapshots.

* `profile_path`: the path to the profile directory to use when running Firefox. This directory contains the Greasemonkey user scripts and the `pluginreg.dat` file generated by `browse.py`.

* `preferences`: the Firefox preferences to be set before running Firefox. You can see the available preferences in the `about:config` page. This option is critical for scouting and recording snapshots. Avoid changing any preferences unnecessarily.

* `extensions_path`: the path to the Firefox extensions directory.

* `extensions_before_running`: any Firefox extensions that require restarting the browser. Installing larger extensions before running can also reduce the time it takes to start Firefox, even if they don't require it.

* `extensions_after_running`: any Firefox extensions that can run immediately after being installed while using the browser.

* `user_scripts`: the Greasemonkey user scripts to install before running Firefox. The names of the user scripts can be found in the [Greasemonkey configuration file](Data/Profile/gm_scripts/config.xml).

* `plugins_path`: the path to the Firefox plugins directory.

* `use_master_plugin_registry`: enable to use a previously generated `pluginreg.dat` file from the `profile_path` directory when running Firefox. This should be disabled temporarily when generating a new file using `browse.py`.

* `plugins`: the NPAPI plugins to use when running Firefox. All filenames must follow the format `np*.dll`. The files must be kept in different directories in order to toggle them individually.

* `shockwave_renderer`: the renderer used by the Shockwave Player. May be `auto`, `software`, `opengl`, `directx 5`, `directx 7`, `directx 9`, or `directx` (same as `directx 9`). It's recommended that you leave this set to `auto`.

* `java_show_console`: enable to show a console for each Java applet. Only used in debug mode.

* `java_add_to_path`: enable to add the path of the autodetected Java directory directory to the PATH environment variable. Used by the Screen Capture Recorder device when recording the screen with FFmpeg.

* `java_arguments`: any Java command line arguments to pass to every applet.

* `cosmo_player_show_console`: enable to show a console for each VRML world. Only used in debug mode.

* `cosmo_player_renderer`: the renderer used by the Cosmo Player. May be `auto`, `directx`, or `opengl`.

* `cosmo_player_animate_transitions`: enable to animate the transitions between viewpoints in a VRML world. Otherwise, snap between viewpoints.

* `_3dvia_renderer`: the renderer used by the 3DVIA Player. May be `auto`, `hardware`, or `software`. It's recommended that you leave this set to `auto`.

* `autoit_path`: the path to the compiled AutoIt scripts directory.

* `autoit_poll_frequency`: how often to poll for new windows when executing the AutoIt scripts (in milliseconds).

* `autoit_scripts`: the AutoIt scripts that execute in the background while Firefox is running.

* `fonts_path`: the path to the fonts directory. Every TTF file here is added to Firefox's fonts directory before running. Used to support custom fonts in old snapshots.

* `sound_fonts_path`: the path to the SoundFonts directory. The scripts choose one random SF2 file from this directory when converting MIDI to WAV.

* `recordings_path`: the path to the snapshot recordings directory.

* `max_recordings_per_directory`: the maximum amount of recordings to be stored in each subdirectory in `recordings_path`.

* `compilations_path`: the path to the recording compilations directory.

* `wayback_machine_rate_limit_amount`: the maximum amount of requests that can be made to the Wayback Machine in a given time window.

* `wayback_machine_rate_limit_window`: the size of the window used by `wayback_machine_rate_limit_amount` (in seconds).

* `cdx_api_rate_limit_amount`: the maximum amount of requests that can be made to the CDX API in a given time window.

* `cdx_api_rate_limit_window`: the size of the window used by `cdx_api_rate_limit_amount` (in seconds).

* `save_api_rate_limit_amount`: the maximum amount of requests that can be made to the Save API in a given time window.

* `save_api_rate_limit_window`: the size of the window used by `save_api_rate_limit_amount` (in seconds).

* `rate_limit_poll_frequency`: how often to poll the rate limit status when making requests to the Wayback Machine, CDX API, and Save API (in seconds).

* `wayback_machine_retry_backoff`: the backoff factor used when retrying Wayback Machine requests as specified by [this formula](https://urllib3.readthedocs.io/en/latest/reference/urllib3.util.html#urllib3.util.Retry). For example, a backoff of 60 would result in waiting 0s, 60s, 120s, 240s, etc.

* `wayback_machine_retry_max_wait`: the maximum amount of time to wait between retrying Wayback Machine requests (in seconds).

* `allowed_domains`: a list of domains that can be visited when scouting and recording snapshots. Domains can have different granularities (e.g. `example.com`, `cdn.example.com`, etc). If null or empty, this option is ignored.

* `disallowed_domains`: a list of domains that cannot be visited when scouting and recording snapshots. Domains can have different granularities (e.g. `example.com`, `cdn.example.com`, etc). If null or empty, this option is ignored.

* `enable_fallback_encoding`: enable to try to set an appropriate fallback character encoding before loading each page.

* `use_guessed_encoding_as_fallback`: enable to use the character encoding guessed by the Wayback Machine as the fallback. Only used if `enable_fallback_encoding` is enabled.

* `ffmpeg_path`: the path to the FFmpeg executables directory. May be null if FFmpeg is already in the PATH.

* `fluidsynth_path`: the path to the FluidSynth executable directory. May be null if FluidSynth is already in the PATH.

* `user_agent`: the user agent sent with each request from FFmpeg, FFprobe, and the requests library. This is not used by Firefox and is only sent when handling media files.

* `language_names`: maps a language's ISO 639-1 code to its name. This currently only lists the languages supported by the Microsoft Speech API.

### Scout

Used by `scout.py`.

* `scheduler`: a cron-like scheduler used when scouting snapshots in batches (in UTC). See [this page](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html) for more details.

* `num_snapshots_per_scheduled_batch`: how many snapshots to scout when executing a scheduled batch.

* `extension_filter`: which extensions from `extensions_before_running` and `extensions_after_running` to install before scouting. This is currently only used to install user scripts via the Greasemonkey extension.

* `user_script_filter`: which user scripts from `user_scripts` to install before scouting. This is currently only used to disable any JavaScript functions that may prevent the WebDriver from working correctly via the `Disable Prompt Functions` user script.

* `initial_snapshots`: a list of snapshots (URL and timestamp) to be used as the starting point when scouting. These snapshots are always scouted first regardless of the following filtering options. Only used if the `-initial` argument is passed to the script.

* `ranking_max_points`: the maximum amount of points to use when ranking a snapshot. May be null if there's no maximum.

* `ranking_offset`: how much to increase the likelihood that a snapshot with no points is scouted next. If this is zero, snapshots whose parents have a positive total amount of points are more likely to be chosen by the weighted random sampling algorithm. In this case, snapshots whose total is zero points are ranked last. As this option increases, these zero-point snapshots can be ranked higher than other ones and may be chosen by the algorithm. If this option is null, snapshots are picked at random regardless of their parents' point total.

* `min_year`: the minimum year for a snapshot to be scouted (inclusive). May be null if there's no minimum.

* `max_year`: the maximum year for a snapshot to be scouted (inclusive). May be null if there's no maximum.

* `max_depth`: the maximum depth for a snapshot to be scouted (inclusive). This depth is measured relative to the snapshots in `initial_snapshots`. May be null if there's no maximum.

* `max_required_depth`: the maximum depth for which snapshots are prioritized (inclusive). Snapshots in this range are always scouted first until they're exhausted. May be null if there's no maximum.

* `min_snapshots_for_same_host`: the minimum amount of scouted page snapshots before the same host can be selected again. Media snapshots are not counted since a single page can link to multiple media files on the same host. May be null if there's no minimum.

* `excluded_url_tags`: which HTML tags to skip when collecting URLs from their attributes.

* `store_all_words_and_tags`: enable to store every word and tag from a snapshot's page in the database. Note that this will substantially increase the database's size. If disabled, only the words and tags in `word_points` and `tag_points` are stored.

* `word_points`: how many points each word is worth. Each word is only counted once per page when computing the total amount of points.

* `tag_points`: how many points each tag is worth. Tags are counted multiple times per page when computing the total amount of points.

* `media_points`: how many points each media snapshot is worth.

* `sensitive_words`: a list of words that would label a snapshot as sensitive. A word may be prefixed with `b64:` if it's encoded in Base64.

* `detect_page_language`: enable to automatically detect each page's language from its content.

* `language_model_path`: the path to the language identification model file. Only used if `detect_page_language` is enabled.

* `tokenize_japanese_text`: enable to tokenize Japanese text before storing the collected words.

### Record

Used by `record.py`, `compile.py`, `voices.py`, and `wayback_proxy_addon.py`.

* `scheduler`: a cron-like scheduler used when recording snapshots in batches (in UTC). See [this page](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html) for more details.

* `num_snapshots_per_scheduled_batch`: how many snapshots to record when executing a scheduled batch.

* `ranking_offset`: how much to increase the likelihood that a snapshot with no points is recorded next. If this is zero, snapshots with a positive amount of points are more likely to be chosen by the weighted random sampling algorithm. In this case, snapshots with zero points are ranked last. As this option increases, these zero-point snapshots can be ranked higher than other ones and may be chosen by the algorithm. If this option is null, snapshots are picked at random regardless of their points.

* `min_year`: the minimum year for a snapshot to be recorded (inclusive). May be null if there's no minimum.

* `max_year`: the maximum year for a snapshot to be recorded (inclusive). May be null if there's no maximum.

* `record_sensitive_snapshots`: enable to allow sensitive snapshots to be recorded.

* `min_recordings_for_same_host`: the minimum amount of recordings before the same host can be selected again. May be null if there's no minimum.

* `min_publish_days_for_same_url`: the minimum amount of days after publishing a recording before the same URL can be selected again. May be null if there's no minimum.

* `allowed_media_extensions`: a list of file extensions that are allowed when recording a media snapshot. This must only include media formats supported by FFmpeg or the browser's plugins.

* `multi_asset_media_extensions`: a list of file extensions whose media format may request additional assets when played. For example, VRML worlds fit this category since they may require textures and sounds. Conversely, playing MIDI music only requires the file itself. Must be a subset of `allowed_media_extensions`.

* `enable_proxy`: enable to pass all browser and plugin traffic through an HTTP proxy. Used for various purposes, including waiting for slow-loading plugin media to finish requesting assets (e.g. Java applets) and locating missing assets in other subdomains via the Wayback Machine CDX API.

* `proxy_port`: the proxy's port. Set to null to automatically find a free port. Only used if `enable_proxy` is enabled.

* `proxy_queue_timeout`: how long to wait between HTTP requests before assuming the page has finished loading (in seconds). Only used if `enable_proxy` is enabled.

* `proxy_total_timeout`: how long to wait after starting monitoring the HTTP traffic before assuming the page has finished loading (in seconds). Only used if `enable_proxy` is enabled.

* `proxy_block_requests_outside_internet_archive`: enable to block any requests outside the `archive.org` domain. Only used if `enable_proxy` is enabled.

* `proxy_convert_realmedia_metadata_snapshots`: enable to convert a RealMedia metadata file (.RAM) to its corresponding audio or video file (.RA or .RM). Useful when recording media snapshots that would otherwise point to a text file instead of the binary media files. Only used if `enable_proxy` is enabled.

* `proxy_find_missing_snapshots_using_cdx`: enable to try to locate missing assets via the Wayback Machine CDX API. This includes searching different paths in all archived subdomains or using the same URL without the query and fragment. Only used if `enable_proxy` is enabled.

* `proxy_max_cdx_path_components`: the maximum amount of components to use starting from the end of the path when performing the previous search. For example, if this value is two and the missing URL is `http://www.example.com/path/to/the/file.ext`, then the proxy will search all `example.com` subdomains for the nearest snapshot whose path ends with `/the/file.ext`. May be null if the whole path should be used (`/path/to/the/file.ext` using the previous example). Only used if `proxy_find_missing_snapshots_using_cdx` is enabled.

* `proxy_save_missing_snapshots_that_still_exist_online`: enable to save any assets that were not archived by the Wayback Machine but that are still available online. Only used if `enable_proxy` is enabled.

* `proxy_max_consecutive_save_tries`: the maximum amount of consecutive tries when saving a live URL with a numbered filename. After recording a snapshot, the script goes through any missing URLs and checks if their filenames contain numbers between the name and extension. If so, it increments this value and also tries to archive the new URL (e.g. if `file01.ext` exists, then we check for `file02.ext` and so on). If a new URL cannot be found online after this many tries, the process stops. Only used if `proxy_save_missing_snapshots_that_still_exist_online` is enabled.

* `proxy_max_total_save_tries`: the maximum amount of total tries when saving a live URL with a numbered filename. The process described above stops if the script exceeds this many tries, even if we keep finding consecutive live URLs. This is done to prevent infinite loops when dealing with parked domains, i.e., when there's a valid response for every possible consecutive number. Only used if `proxy_save_missing_snapshots_that_still_exist_online` is enabled.

* `proxy_cache_missing_responses`: enable to try to cache 404 and 410 responses from the Wayback Machine. Used to potentially speed up the loading times when recording a snapshot. Only used if `enable_proxy` is enabled.

* `check_availability`: enable to check if the Wayback Machine and CDX API are available before recording. If this is enabled and these services are down, the script will keep retrying indefinitely. While it's recommended that you leave this option enabled, there have been cases where this check says the services are down when snapshots can be loaded successfully. In those situations, this option should be temporarily disabled so the script doesn't hang forever.

* `hide_scrollbars`: enable to hide the scrollbars of every page element.

* `page_cache_wait`: how long to wait for a page snapshot to finish requesting and caching assets before recording (in seconds).

* `media_cache_wait`: how long to wait for a media snapshot to finish requesting and caching assets before recording (in seconds).

* `plugin_load_wait`: how long to wait for plugins to start running after loading the snapshot (in seconds). This should be a small value to make sure that most plugins have time to load.

* `base_plugin_crash_timeout`: how long to wait without receiving a response from the browser before killing all plugin processes (in seconds). Used to force the script to continue when a plugin crashes. The script waits this amount plus the expected caching or recording duration.

* `viewport_scroll_percentage`: how much to scroll the browser window based on the viewport's height when recording. Only used by page snapshots.

* `base_wait_after_load`: how long to wait after loading the page when recording (in seconds). Only used by page snapshots.

* `wait_after_load_per_plugin_instance`: how much longer to wait after loading the page per plugin instance when recording (in seconds). Only used by page snapshots.

* `base_wait_per_scroll`: how long to wait after scrolling the page when recording (in seconds). Only used by page snapshots.

* `wait_after_scroll_per_plugin_instance`: how much longer to wait after scrolling the page per plugin instance when recording (in seconds). Only used by page snapshots.

* `wait_for_plugin_playback_after_load`: enable to find the duration of the longest embedded media file and wait as long as possible so that the recording captures most of it. Useful for short pages that play long audio files.

* `base_media_wait_after_load`: how long to wait after loading the page that embeds the media when recording (in seconds). The scripts waits this amount plus the total media file's duration. This should be a small value to prevent the final recording from being too short. Only used by media snapshots.

* `media_fallback_duration`: the fallback value used when the media file's duration cannot be determined (in seconds). This is mostly used by Flash movies, Shockwave movies, and VRML worlds. Only used by media snapshots.

* `media_width`: the width of the embedded media file as a percentage of the page's width. This should be slightly lower than `100%` to avoid showing scrollbars. Only used by media snapshots.

* `media_height`: the height of the embedded media file as a percentage of the page's height. This should be slightly lower than `100%` to avoid showing scrollbars. Only used by media snapshots.

* `media_background_color`: the background color of the page that embeds the media file (in hexadecimal). For Flash movies and any file formats supported by VLC, this value also sets the background color of the media itself. Only used by media snapshots.

* `plugin_syncing_page_type`: the method used to sync plugin media so that different page elements start playing at the same time. Set to `reload_before` to restart all plugin media before recording. Set to `reload_twice` to do the same as `reload_before` while also reloading a second time immediately after the recording starts. Useful for pages that play a short audio file after loading. Set to `unload` to only start playing plugin media after the recording starts. Set to `none` to disable this feature. No other values are allowed. While `unload` is meant to be a more robust version of `reload_before`, the underlying implementation may not always work correctly.

* `plugin_syncing_media_type`: same as `plugin_syncing_page_type` but for media snapshots.

* `plugin_syncing_unload_delay`: how long to wait to start playing plugin media after the recording begins (in seconds). This should be a small value, preferably under one second. Only used when `plugin_syncing_page_type` or `plugin_syncing_media_type` are set to `unload`.

* `plugin_syncing_reload_vrml_from_cache`: enable to force VRML worlds to be reloaded from cache before recording. This is done to prevent some issues between the Cosmo Player and the previous plugin syncing methods. If enabled, this action is only performed if one or more Cosmo Player instances are running. This option is not affected by `plugin_syncing_page_type` or `plugin_syncing_media_type`.

* `enable_plugin_input_repeater`: enable to periodically interact with Flash movies and Java applets by sending them keyboard events.

* `plugin_input_repeater_initial_wait`: how long to wait before sending the first keyboard event (in seconds). Only used if `enable_plugin_input_repeater` is enabled.

* `plugin_input_repeater_wait_per_cycle`: how long to wait between keyboard events (in seconds). Only used if `enable_plugin_input_repeater` is enabled.

* `plugin_input_repeater_min_window_size`: the minimum plugin instance dimensions for the media to be considered interactable. Used to exclude plugin media that redirects the browser to a different page when clicked (e.g. small Flash ads). Only used if `enable_plugin_input_repeater` is enabled.

* `plugin_input_repeater_keystrokes`: which keyboard events to send. This must be a string containing each key code in order as documented on [this page](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.keyboard.html). Only used if `enable_plugin_input_repeater` is enabled.

* `plugin_input_repeater_debug`: enable to display debug information about each plugin instance. If an instance is interactable, a green rectangle is drawn around the plugin media. Otherwise, a red rectangle is used. The plugin media's width and height are also shown on top of it. Only used if `debug` is enabled.

* `enable_cosmo_player_viewpoint_cycler`: enable to periodically cycle through all viewpoints in VRML worlds.

* `cosmo_player_viewpoint_wait_per_cycle`: how long to wait between viewpoints (in seconds). Only used if `enable_cosmo_player_viewpoint_cycler` is enabled.

* `min_duration`: the minimum recording duration (in seconds). Used to stay within the Twitter, Mastodon, and Tumblr media guidelines.

* `max_duration`: the maximum recording duration (in seconds). Used to stay within the Twitter, Mastodon, and Tumblr media guidelines.

* `save_archive_copy`: enable to save a lossless copy of the raw recording for archival purposes. Although this copy is smaller than the raw footage, it's still significantly larger than the lossy recording.

* `screen_capture_recorder_settings`: the Screen Capture Recorder device settings. Any previous settings are temporarily cleared from the registry before applying these changes. As such, you should only change a setting if you don't want its default value. If `capture_width` or `capture_height` are null, these will be set to the screen's physical width and height, respectively, in order to record the entire screen. If `default_max_fps` is null, it will be set to the `-framerate` parameter from `raw_ffmpeg_input_args`. If this parameter isn't specified, then the setting is set to 60. Note that this is just the device's maximum frame rate. The recording frame rate is specified in `raw_ffmpeg_input_args`. Refer to the [Screen Capture Recorder documentation](https://github.com/rdp/screen-capture-recorder-to-video-windows-free#configuration) for a list of all possible settings.

* `raw_ffmpeg_input_name`: the input device used to capture the screen. This should be the Screen Capture Recorder device.

* `raw_ffmpeg_input_args`: the input arguments used to capture the screen. Refer to the [FFmpeg documentation](https://ffmpeg.org/ffmpeg-all.html) for a list of all possible parameters. In particular, see [this tutorial](https://trac.ffmpeg.org/wiki/Capture/Desktop) to learn about the best parameters to use when capturing the screen with FFmpeg.

* `raw_ffmpeg_output_args`: the output arguments used to generate the raw recording footage.

* `archive_ffmpeg_output_args`: the output arguments used to generate a lossless recording from the raw footage. Only used if `save_archive_copy` is enabled.

* `upload_ffmpeg_output_args`: the output arguments used to generate a lossy recording from the raw footage. Refer to the recommended audio and video encoding settings of the supported platforms before changing any parameters: [YouTube](https://support.google.com/youtube/answer/1722171), [Twitter](https://developer.twitter.com/en/docs/twitter-api/v1/media/upload-media/uploading-media/media-best-practices), [Mastodon](https://docs.joinmastodon.org/user/posting/#attachments), [Tumblr](https://help.tumblr.com/hc/en-us/articles/231455628-Adding-Video).

* `enable_text_to_speech`: enable to generate a text-to-speech recording of a page snapshot's content.

* `text_to_speech_audio_format_type`: the text-to-speech audio format. Must be an enum name from [this page](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee125189(v=vs.85)) (e.g. `SAFT22kHz16BitMono`). Set to null to use the default format. Only used if `enable_text_to_speech` is enabled.

* `text_to_speech_rate`: the text-to-speech voice's speaking rate. Must be a number between -10 and 10 as mentioned on [this page](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ms723606(v=vs.85)). Set to null to use the default rate. Only used if `enable_text_to_speech` is enabled.

* `text_to_speech_default_voice`: the name of the Microsoft Speech API voice to use for an unsupported language. In most cases, the English (United States) voices (`David`, `Mark`, `Zira`) should be available. Set to null to leave the default voice unchanged. Only used if `enable_text_to_speech` is enabled.

* `text_to_speech_language_voices`: the names of the Microsoft Speech API voices to use for the supported languages. Each language's voice package must be installed first. Otherwise, the default voice is used. Note that this feature requires enabling `detect_page_language` before scouting snapshots. Only used if `enable_text_to_speech` is enabled.

* `text_to_speech_ffmpeg_video_input_name`: the input video stream shown during the text-to-speech recording. Although this recording only requires an audio stream, the video component is added for platforms that don't support the former (e.g. Twitter). Only used if `enable_text_to_speech` is enabled.

* `text_to_speech_ffmpeg_video_input_args`: the input video arguments used when generating a text-to-speech recording. Only used if `enable_text_to_speech` is enabled.

* `text_to_speech_ffmpeg_audio_input_args`: the input audio arguments used when generating a text-to-speech recording. Only used if `enable_text_to_speech` is enabled.

* `text_to_speech_ffmpeg_output_args`: the output arguments used to generate a text-to-speech recording. Only used if `enable_text_to_speech` is enabled.

* `enable_media_conversion`: enable to convert media snapshots into the final recording format without capturing the screen. Used to save time and avoid synchronization issues when recording audio and video file formats.

* `media_conversion_extensions`: a list of file extensions whose format can be directly converted into the recording. Must be a subset of `allowed_media_extensions` and mutually exclusive from `multi_asset_media_extensions`. Only used if `enable_media_conversion` is enabled.

* `media_conversion_ffmpeg_input_name`: the input video stream shown during audio-only media formats. The same platform restrictions from `text_to_speech_ffmpeg_video_input_name` also apply here. Only used if `enable_media_conversion` is enabled.

* `media_conversion_ffmpeg_input_args`: the input video arguments used when converting the media snapshot. The corresponding output arguments are defined in `upload_ffmpeg_output_args`. Only used if `enable_media_conversion` is enabled.

* `media_conversion_add_subtitles`: enable to add subtitles with the file's metadata to the video stream when converting audio-only media formats. Used to make media snapshots without a video component more interesting.

* `media_conversion_ffmpeg_subtitles_style`: how to style the media snapshot's subtitles. This must be a string containing Advanced Substation Alpha / Substation Alpha (ASS/SSA) style fields separated by commas. Refer to the [libass source code](https://github.com/libass/libass/blob/master/libass/ass_types.h) and the [Sub Station Alpha v4.00+ Script Format](https://web.archive.org/web/20230209193228if_/https://forum.videohelp.com/attachment.php?attachmentid=33290&d=1440307546) for a list of all possible fields. Only used if `media_conversion_add_subtitles` is enabled.

* `enable_audio_mixing`: enable to replace the audio of the final recording with a mix of all the audio-only files embedded on a snapshot's page. This is only done if all the files have exclusively audio streams (i.e. not videos or Flash movies). Media files that weren't archived are ignored.

* `audio_mixing_ffmpeg_output_args`: the output arguments used when mixing the audio. Only used if `enable_audio_mixing` is enabled.

* `midi_fluidsynth_args`: the arguments used to convert MIDI to WAV. Refer to the [FluidSynth documentation](https://man.archlinux.org/man/fluidsynth.1.en) for a list of all possible parameters.

### Publish

Used by `publish.py` and `approve.py`.

* `scheduler`: a cron-like scheduler used when publishing recordings in batches (in UTC). See [this page](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html) for more details.

* `num_recordings_per_scheduled_batch`: how many recordings to publish when executing a scheduled batch.

* `enable_twitter`: enable to publish on Twitter.

* `enable_mastodon`: enable to publish on Mastodon.

* `enable_tumblr`: enable to publish on Tumblr.

* `require_approval`: enable to only publish recordings that have been manually approved using `approve.py`.

* `reply_with_text_to_speech`: enable to add the text-to-speech recording as a reply to the post. When publishing on Twitter, the recording may have to be split into multiple replies. Not supported when publishing on Tumblr.

* `delete_files_after_upload`: enable to delete the recording file after being uploaded to all platforms.

* `twitter_api_key`: the API key obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Twitter.**

* `twitter_api_secret`: the API secret obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Twitter.**

* `twitter_access_token`: the access token obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Twitter.**

* `twitter_access_token_secret`: the access token secret obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Twitter.**

* `twitter_api_wait`: how long to wait after making a request to the Twitter API (in seconds). This was added to reduce the chance of being flagged by the Twitter spam algorithm, though it probably doesn't do too much in practice.

* `twitter_max_retries`: the maximum amount of times to retry a Twitter API request when an unexpected error occurs.

* `twitter_retry_wait`: how long to wait before retrying a Twitter API request (in seconds).

* `twitter_max_status_length`: the maximum amount of characters in a Twitter post. This should be set to the current Twitter character limit.

* `twitter_text_to_speech_segment_duration`: the maximum duration of each segment when splitting the text-to-speech recordings (in seconds). This should be set to slightly under the current Twitter video duration limit.

* `twitter_max_text_to_speech_segments`: the maximum amount of text-to-speech segments (i.e. replies) to post. If the recording requires more than this amount, the text-to-speech replies are skipped. May be null if there's no maximum.

* `mastodon_instance_url`: the instance's URL decided in the [setup guide](#setup-guide). **Must be changed before publishing on Mastodon.**

* `mastodon_access_token`: the access token obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Mastodon.**

* `mastodon_max_retries`: the maximum amount of times to retry a Mastodon API request when an unexpected error occurs.

* `mastodon_retry_wait`: how long to wait before retrying a Mastodon API request (in seconds).

* `mastodon_max_status_length`: the maximum amount of characters in a Mastodon post. This should be set to the instance's character limit.

* `mastodon_max_file_size`: the maximum size of each recording file (in megabytes). This should be set to the instance's video size limit. May be null if there's no maximum.

* `mastodon_reduce_file_size`: enable to run every recording through FFmpeg in order to reduce the file size. It's strongly recommended that you enable this option since Mastodon's default limit would otherwise exclude a lot of recordings. Additionally, this reduces the total amount of disk space used by the bot in the instance.

* `mastodon_reduce_file_size_ffmpeg_output_args`: the output arguments used to reduce the file size. Aside from using filters, one trick is to avoid specifying any output bitrates so that FFmpeg reencodes the files using the default values. In most cases, this should lower the file size significantly. Only used if `mastodon_reduce_file_size` is enabled.

* `tumblr_api_key`: the consumer key obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Tumblr.**

* `tumblr_api_secret`: the consumer secret obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Tumblr.**

* `tumblr_access_token`: the token obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Tumblr.**

* `tumblr_access_token_secret`: the token secret obtained in the [setup guide](#setup-guide). **Must be changed before publishing on Tumblr.**

* `tumblr_max_retries`: the maximum amount of times to retry a Tumblr API request when an unexpected error occurs.

* `tumblr_retry_wait`: how long to wait before retrying a Tumblr API request (in seconds).

* `tumblr_max_status_length`: the maximum amount of characters in a Tumblr post. This should be set to the current Tumblr character limit.

## Custom Options

Some of the options described above can be changed for specific snapshots using the `Options` column in the database. This column takes a JSON object with any options to override the default configuration. For example, if you wanted a recording to last longer and wanted to improve the chances of a short audio file being captured correctly, you could use the following: `{"min_duration": 60, "plugin_syncing_page_type": "reload_twice"}`. You can find a list of all mutable options in [`common/config.py`](common/config.py#L104).

This column also accepts the following extra options:

* `encoding`: force a specific fallback character encoding. This takes precedence over the guessed encoding from the Wayback Machine. Only used if `enable_fallback_encoding` is enabled.

* `media_extension_override`: force a specific file extension for a media snapshot. Used in rare cases where a snapshot has an incorrect extension that affects how the media is displayed (e.g. `ram` instead of `rm`).

* `notes`: notes and comments about the snapshot.

* `script`: JavaScript code to execute after loading the snapshot's page but before the recording starts.

* `tags`: a list of tags to add to the Tumblr post. Can be used to group snapshots by theme and for content warnings (e.g. `["halloween", "jumpscare"]`).