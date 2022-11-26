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

* [brotlicffi](https://github.com/python-hyper/brotlicffi): to automatically decompress Brotli-encoded requests.

* [limits](https://github.com/alisaifee/limits): to avoid making too many requests to the Wayback Machine, the CDX API, and the Save API.

* [ffmpeg-python](https://github.com/kkroening/ffmpeg-python): to record the screen and manipulate audio/video files.

* [Tweepy](https://github.com/tweepy/tweepy): to upload the recorded videos to Twitter and publish tweets.

* [Mastodon.py](https://github.com/halcy/Mastodon.py): to upload the recorded videos to Mastodon and publish toots.

* [APScheduler](https://github.com/agronholm/apscheduler): to schedule the scouting, recording, and publishing scripts.

* [fastText](https://github.com/facebookresearch/fastText): to detect a page's language from its text. Only used if `detect_page_language` is enabled.

* [fugashi](https://github.com/polm/fugashi): to tokenize Japanese text scraped from a page. Only used if `tokenize_japanese_text` is enabled.

* [comtypes](https://github.com/enthought/comtypes): to interface with Windows' text-to-speech API and generate audio recordings from a page's content. Only used if `enable_text_to_speech` is enabled, though it should already be installed since pywinauto depends on it.

* [mitmproxy](https://github.com/mitmproxy/mitmproxy) to intercept all HTTP/HTTPS requests made by Firefox and its plugins. Used to locate missing resources in other subdomains via the CDX API while also allowing plugin media that loads slowly to finish requesting assets. Only used if `enable_proxy` is enabled.

* [tldextract](https://github.com/john-kurkowski/tldextract) to determine the correct registered domain from a URL. Only used if `enable_proxy` is enabled.

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

10. Download and install the [Screen Capturer Recorder](https://github.com/rdp/screen-capture-recorder-to-video-windows-free/releases) device in order to capture the screen using FFmpeg. Note that this program requires Java. You can either use a modern Java install or reuse the local Java install from step 7. If you choose the latter, make sure to enable `java_add_to_path` so that the Java directory path (e.g. `Data/Plugins/Java/jdk1.8.0_11/jre/bin` or `Data/Plugins/Java/jre1.8.0_11/bin`) is added to the PATH automatically.

11. If you want to automatically detect a page's language, enable `detect_page_language`, download a [language identification model](https://fasttext.cc/docs/en/language-identification.html) to `Data`, and enter its path in `language_model_path`.

12.	If you want to generate the text-to-speech audio recordings, enable `enable_text_to_speech` and install any missing voice packages in the Windows settings by going to `Ease of Access > Speech (under Interaction) > Additional speech settings (under Related settings) > Add Voices (under Manage voices)`. Note that just installing the packages isn't enough to make the voices visible to the Microsoft Speech API. You can run the following script to generate a REG file that will automatically add all installed voices to the appropriate registry key: `voices.py -registry`. Execute the resulting `voices.reg` file and then run the following script to list every visible voice: `voices.py -list`. The script will warn you if it can't find a voice specified in `text_to_speech_language_voices`. The configuration template lists every language available in the Windows 10 speech menu at the time of writing. You can also use the `-speak` argument together with `-list` to test each voice and make sure it works properly. Run `voices.py -list -speak all` to test all voices or `voices.py -list -speak "spanish (mexico)"` (for example) to test a specific language. If a voice test fails with the error `COMError -2147200966`, you must install that voice's language package in the Window settings by going to `Time & Language > Language > Add a language (under Preferred languages)`. You only have to install the text-to-speech feature in each package.

13. If you want to approve the recordings before publishing them to Twitter or Mastodon (i.e. if `require_approval` is enabled), you can set the portable VLC version installed in step 6 as the default MP4 file viewer.

14. To publish the recorded videos on Twitter, create an account for the bot, log into the [Twitter Developer Platform](https://developer.twitter.com/en), and apply for elevated access on the dashboard. Then, create a new project and application, set up OAuth 1.0a authentication with at least read and write permissions, and generate an access token and access token secret. Enter your application's API key, API secret, and the previous tokens into `twitter_api_key`, `twitter_api_secret`, `twitter_access_token`, and `twitter_access_token_secret`, respectively. Alternatively, you can set these options to null and place the tokens in the `WANDERER_TWITTER_API_KEY`, `WANDERER_TWITTER_API_SECRET`, `WANDERER_TWITTER_ACCESS_TOKEN`, and `WANDERER_TWITTER_ACCESS_TOKEN_SECRET` environment variables. At the time of writing, you need to use the standard Twitter API version 1.1 to upload videos. This requires having both elevated access and using OAuth 1.0a.

15. To publish the recorded videos on Mastodon, create an account for the bot in an appropriate instance. Choose either an instance your hosting yourself or one that was designed specifically for bots. Then, go to `Settings > Development` and create a new application. While doing so, select the `write:media` and `write:statuses` scopes and uncheck any others. Save these changes and copy the generated access token to `mastodon_access_token`. Alternatively, you can set this option to null and place the token in the `WANDERER_MASTODON_ACCESS_TOKEN` environment variable. Finally, set `mastodon_instance_url` to the instance's URL. It's strongly recommended that you enable automated post deletion on your account with a threshold of one or two weeks.

### Additional Steps For Remote Machines

If you're hosting the bot in a remote Windows machine, there are some additional steps you may want to follow.

* It's recommended that you connect and control the machine via Virtual Network Computing (VNC) rather than Remote Desktop Protocol (RDP). When you disconnect from an RDP session, the GUI is no longer available which breaks any component that relies on interacting with it (e.g. FFmpeg when capturing the screen, pywinauto when focusing on a browser window or moving the mouse). While there are some workarounds for this using `tscon` and the `HKEY_LOCAL_MACHINE\Software\Microsoft\Terminal Server Client` registry key, using VNC seemed like the simpler and more robust choice. See [this page](https://stackoverflow.com/questions/15887729/can-the-gui-of-an-rdp-session-remain-active-after-disconnect) for more details. If `require_approval` is enabled, you might still prefer using RDP to check the recordings since it supports audio while VNC does not. Make sure to connect via VNC after disconnecting from an RDP session, otherwise the bot won't be able to record the screen. Ensure also that you run the recorder script while connected via VNC so it retrieves the correct screen resolution and DPI scaling while initializing. Note that your remote machine must be running Windows Pro edition in order to be controlled via RDP.

* If your machine doesn't have any audio output devices, then some components will crash or show error messages during recording. These include the FFmpeg audio capture device and the MIDI web plugin. You can solve this by installing the [VB-CABLE](https://vb-audio.com/Cable/index.htm) virtual audio device and selecting the speakers as your default output device in the Windows settings by going to `Devices > Sound settings (under Related settings) > Sound Control Panel (under Related settings) > Playback`.

* It's strongly recommended that you increase the `rtbufsize` and `thread_queue_size` parameters in `ffmpeg_recording_input_args` to the maximum supported values for the remote machine. Check how much RAM is free while displaying a page with plugins in the browser (e.g. [this snapshot](https://web.archive.org/web/19990222174035if_/http://www.geocities.com/Heartland/Plains/1036/arranco.html)) and set `rtbufsize` somewhere close to that value. If you see the errors `real-time buffer [screen-capture-recorder] [video input] too full or near too full` or `Thread message queue blocking; consider raising the thread_queue_size option` in the recorder log file, increase `rtbufsize` and `thread_queue_size`, respectively. If these values are too low, the recordings will stutter. A good rule of thumb is setting `thread_queue_size` to 5000 and `rtbufsize` as close as possible to 2 GB while leaving around 400 to 500 MB free for Firefox, the web plugins, and the Python scripts.

* If the recordings stutter in specific situations (e.g. VRML worlds or video media files), consider lowering the frame rate from 60 to 30 FPS. You can do this by setting the `framerate` parameter in `ffmpeg_recording_input_args` to 30, and by changing the `r` and `g` parameters in `ffmpeg_upload_output_args` to 30 and 15, respectively. Remember that `g` should be half the frame rate. If `enable_media_conversion` is enabled, change the `rate` parameter in `ffmpeg_media_conversion_input_name` from `60/1` to `30/1`.

* It's recommended that you adjust the scale filter dimensions in `ffmpeg_upload_output_args` depending on your screen capture dimensions (or on your display settings if these are set to null in `screen_capture_recorder_settings`). For example, you could change the scale filter width and height to 1920x1080 (16:9) or 1440x1080 (4:3) in order to record 1080p videos. If `enable_media_conversion` is enabled, you should also change the `size` parameter in `ffmpeg_media_conversion_input_name`.

* Check if your machine can record pages with plugins properly while `plugin_syncing_page_type` is set to `unload`. If you notice any issues like embedded audio files not being played correctly, set this option to `reload`. These steps also apply to `plugin_syncing_media_type`, meaning this option should stay set to `unload` unless you notice a problem while recording media file snapshots.

* Depending on the remote machine you're using to host the bot, it's possible that you won't be able to use the OpenGL renderer when viewing VRML worlds with the Cosmo Player. If that's the case, you should change the renderer to DirectX by setting `cosmo_player_renderer` to `DirectX`. The Shockwave and 3DVIA players are able to choose the best available renderer, meaning `shockwave_renderer` and `3dvia_renderer` can be left to `Auto`.

* Consider disabling any appearance settings that might reduce the remote machine's performance in the Windows settings by going to `System > About > Advanced system settings (under Related settings) > Settings... (under Performance) > Visual Effects` and selecting `Adjust for best performance`. Doing this can also make the text in old pages look sharper.

* If you installed a Windows version without a product key, you should activate it to prevent the watermark from appearing in the recordings.

* If you want Windows to automatically sign into your account after booting then run the command `netplwiz`, uncheck `Users must enter a user name and password to use this computer` in the User Accounts window, and enter your credentials after pressing ok.

* It's recommended that you set your machine's time zone to UTC in the Windows settings by going to `Time & Language > Time zone (under Current data and time) > (UTC) Coordinated Universal Time` and pressing `Sync now`. This should make it easier to track the scheduled jobs executed by the scout, recorder, and publisher scripts.

* It's recommended that you disable any automatic Windows updates to prevent any unwanted restarts while the bot is running. You can do this in the Services settings by going to the `Windows Update` service's properties, setting the startup type to `Disabled`, and then pressing `Stop`. Note that you should only do this after setting up the bot and confirming that it works properly. This is because some of the previous steps may require the Windows Update service. For example, if you tried to install the voice packages after disabling this service, it would fail with the error `The voice package couldn't be installed`.

## Scripts

Below is a summary of the Python scripts located in [the source directory](Source). The first three scripts are the most important ones as they handle the metadata collection, the screen recording, and the video publishing. These were designed to either run forever or only a set number of times. Pass the `-h` command line argument to learn how to use each script.

* `scout.py`: traverses web pages archived by the Wayback Machine (snapshots) and collects metadata from their content and from the CDX API. The scout script prioritizes pages that were manually added by the user through the configuration file as well as pages whose parent snapshot contains specific words and plugin media.

* `record.py`: records the previously scouted snapshots on a set schedule by opening their pages in Firefox and scrolling through them at a set pace. If the recorder script detects that any plugins crashed or that the page was redirected while capturing the screen, the recording is aborted. **This script is inherently unsafe since it relies on web plugins (e.g. Flash, Shockwave, Java, etc).**

* `publish.py`: publishes the previously recorded snapshots to Twitter and Mastodon on a set schedule. The publisher script uploads each snapshot's MP4 video and generates a tweet with the web page's title, its date, and a link to its Wayback Machine capture.

* `approve.py`: approves recordings for publishing. This process is optional and can only be done if the publisher script was started with the `require_approval` option enabled.

* `enqueue.py`: adds a Wayback Machine snapshot to the queue with a given priority. This can be used to scout, record, or publish any existing or new snapshots as soon as possible.

* `compile.py`: compiles multiple snapshot recordings into a single video. This can be done for published recordings that haven't been compiled yet, or for any recordings given their database IDs. A short transition with a user-defined background color, duration, and sound effect is inserted between each recording.

* `delete.py`: deletes all video files belonging to unapproved and/or compiled recordings.

* `browse.py`: opens a URL in a Firefox version equipped with various plugins and extensions. Avoid using this version to browse live websites.

* `save.py`: saves URLs from the standard input using the Wayback Machine Save API.

* `voices.py`: lists and exports the voices used by the Microsoft Speech API.

* `stats.py`: shows snapshot and recording statistics from the database.

* `wayback_proxy_addon.py`: a mitmproxy script that tells the recorder script if the page is still making requests while also checking if any missing files are available in a different subdomain. This script should not be run directly and is instead started automatically by the recorder if `enable_proxy` is enabled.

* `dump_proxy_addon.py`: a mitmproxy script that generates a dump file containing all HTTP/HTTPS responses received by the browser. This script should not be run directly and is instead started automatically by the browser script if the `-dump` argument was used.

* `common.py`: a module that defines any general purpose functions used by all scripts, including loading configuration files, connecting to the database, and interfacing with Firefox.

## Types Of Snapshots

The bot handles two types of snapshots: web pages and media files. The first are any snapshots that were successfully archived by the Wayback Machine (i.e. a 200 status code) and whose MIME type is `text/html` or `text/plain`. The second are any other successfully archived snapshots whose MIME type does *not* match the previous criteria. In other words, any standard and non-standard audiovisual media (e.g. `audio/*`, `video/*`, `application/*`, `x-world/*`, `music/*`, etc). This allows the bot to showcase multimedia (e.g. MIDI music, QuickTime videos, VRML worlds) that was linked directly in a page instead of being embedded with the object and embed tags.

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

* `java_add_to_path`: enable to add the path of the autodetected Java directory directory to the PATH environment variable. Used by the Screen Capturer Recorder device when recording the screen with FFmpeg.

* `java_arguments`: any Java command line arguments to pass to every applet.

* `cosmo_player_show_console`: enable to show a console for each VRML world. Only used in debug mode.

* `cosmo_player_renderer`: the renderer used by the Cosmo Player. May be `auto`, `directx`, or `opengl`.

* `cosmo_player_animate_transitions`: enable to animate the transitions between viewpoints in a VRML world. Otherwise, snap between viewpoints.

* `3dvia_renderer`: the renderer used by the 3DVIA Player. May be `auto`, `hardware`, or `software`. It's recommended that you leave this set to `auto`.

* `autoit_path`: the path to the compiled AutoIt scripts directory.

* `autoit_poll_frequency`: how often to poll for new windows when executing the AutoIt scripts (in milliseconds).

* `autoit_scripts`: the AutoIt scripts that execute in the background while Firefox is running.

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

* `ffmpeg_global_args`: a list of arguments to always pass to FFmpeg. Note that the `-y` argument used to overwrite the output file is always passed.

* `language_names`: maps a language's ISO 639-1 code to its name. This currently only lists the languages supported by the Microsoft Speech API.

### Scout

Used by `scout.py`.

* `scheduler`: a cron-like scheduler used when scouting snapshots in batches (in UTC). See [this page](https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html) for more details.

* `num_snapshots_per_scheduled_batch`: how many snapshots to scout when executing a scheduled batch.

* `extension_filter`: which extensions from `extensions_before_running` and `extensions_after_running` to install before scouting. This is currently only used to install user scripts via the Greasemonkey extension.

* `user_script_filter`: which user scripts from `user_scripts` to install before scouting. This is currently only used to disable any JavaScript functions that may prevent the WebDriver from working correctly via the `Disable Prompt Functions` user script.

* `initial_snapshots`: a list of snapshots (URL and timestamp) to be used as the starting point when scouting. Only used if the `-initial` argument is passed to the script.

* `ranking_offset`: how much to increase the likelihood that a snapshot with no points is scouted next. If this is zero, snapshots whose parents have a positive total amount of points are more likely to be chosen by the weighted random sampling algorithm. In this case, snapshots whose total is zero points are ranked last. As this option increases, these zero-point snapshots can be ranked higher than other ones and may be chosen by the algorithm. If this option is null, snapshots are picked at random regardless of their parents' point total.

* `min_year`: the minimum year for a snapshot to be scouted (inclusive). May be null if there's no minimum.

* `max_year`: the maximum year for a snapshot to be scouted (inclusive). May be null if there's no maximum.

* `max_depth`: the maximum depth for a snapshot to be scouted (inclusive). This depth is measured relative to the snapshots in `initial_snapshots`. May be null if there's no maximum.

* `max_required_depth`: the maximum depth for which snapshots are prioritized (inclusive). Snapshots in this range are always scouted first until they're exhausted. May be null if there's no maximum.

* `excluded_url_tags`: which HTML tags to skip when collecting URLs from their attributes.

* `store_all_words_and_tags`: enable to store every word and tag from a snapshot's page in the database. Note that this will substantially increase the database's size. If disabled, only the words and tags in `word_points` and `tag_points` are stored. 

* `word_points`: how many points each word is worth. Each word is only counted once per page when computing the total amount of points.

* `tag_points`: how many points each tag is worth. Tags are counted multiple times per page when computing the total amount of points.

* `media_points`: how many points each media snapshot is worth.

* `sensitive_words`: a list of words that would label a snapshot as sensitive. A word may be prefixed with `b64:` if its encoded in Base64.

* `detect_page_language`: enable to automatically detect each page's language using its content. Requires a language identification model in `language_model_path`.

* `language_model_path`: the path to the language identification model file.

* `tokenize_japanese_text`: enable to tokenize Japanese text before storing the collected words.

### Record

Used by `record.py`, `compile.py`, `voices.py`, and `wayback_proxy_addon.py`.

* `scheduler`: @TODO.

* `num_snapshots_per_scheduled_batch`: @TODO.

* `ranking_offset`: @TODO.

* `min_year`: @TODO.

* `max_year`: @TODO.

* `record_sensitive_snapshots`: @TODO.

* `min_creation_days_for_same_host`: @TODO.

* `min_publish_days_for_same_snapshot`: @TODO.

* `allowed_media_extensions`: @TODO.

* `multi_asset_media_extensions`: @TODO.

* `enable_proxy`: @TODO.

* `proxy_port`: @TODO.

* `proxy_queue_timeout`: @TODO.

* `proxy_total_timeout`: @TODO.

* `proxy_block_requests_outside_internet_archive`: @TODO.

* `proxy_convert_realmedia_metadata_snapshots`: @TODO.

* `proxy_find_missing_snapshots_using_cdx`: @TODO.

* `proxy_max_cdx_path_components`: @TODO.

* `proxy_save_missing_snapshots_that_still_exist_online`: @TODO.

* `proxy_max_consecutive_save_tries`: @TODO.

* `proxy_max_total_save_tries`: @TODO.

* `proxy_cache_missing_responses`: @TODO.

* `page_cache_wait`: @TODO.

* `media_cache_wait`: @TODO.

* `plugin_load_wait`: @TODO.

* `base_plugin_crash_timeout`: @TODO.

* `viewport_scroll_percentage`: @TODO.

* `base_wait_after_load`: @TODO.

* `wait_after_load_per_plugin_instance`: @TODO.

* `base_wait_per_scroll`: @TODO.

* `wait_after_scroll_per_plugin_instance`: @TODO.

* `base_media_wait_after_load`: @TODO.

* `media_fallback_duration`: @TODO.

* `media_width`: @TODO.

* `media_height`: @TODO.

* `media_background_color`: @TODO.

* `fullscreen_browser`: @TODO.

* `plugin_syncing_page_type`: @TODO.

* `plugin_syncing_media_type`: @TODO.

* `plugin_syncing_unload_delay`: @TODO.

* `plugin_syncing_reload_vrml_from_cache`: @TODO.

* `enable_plugin_input_repeater`: @TODO.

* `plugin_input_repeater_initial_wait`: @TODO.

* `plugin_input_repeater_wait_per_cycle`: @TODO.

* `plugin_input_repeater_min_window_size`: @TODO.

* `plugin_input_repeater_keystrokes`: @TODO.

* `plugin_input_repeater_debug`: @TODO.

* `enable_cosmo_player_viewpoint_cycler`: @TODO.

* `cosmo_player_viewpoint_wait_per_cycle`: @TODO.

* `min_duration`: @TODO.

* `max_duration`: @TODO.

* `keep_archive_copy`: @TODO.

* `screen_capture_recorder_settings`: @TODO.

* `ffmpeg_recording_input_name`: @TODO.

* `ffmpeg_recording_input_args`: @TODO.

* `ffmpeg_recording_output_args`: @TODO.

* `ffmpeg_archive_output_args`: @TODO.

* `ffmpeg_upload_output_args`: @TODO.

* `enable_text_to_speech`: @TODO.

* `text_to_speech_read_image_alt_text`: @TODO.

* `text_to_speech_audio_format_type`: @TODO.

* `text_to_speech_rate`: @TODO.

* `text_to_speech_default_voice`: @TODO.

* `text_to_speech_language_voices`: @TODO.

* `ffmpeg_text_to_speech_video_input_name`: @TODO.

* `ffmpeg_text_to_speech_video_input_args`: @TODO.

* `ffmpeg_text_to_speech_audio_input_args`: @TODO.

* `ffmpeg_text_to_speech_output_args`: @TODO.

* `enable_media_conversion`: @TODO.

* `convertible_media_extensions`: @TODO.

* `ffmpeg_media_conversion_input_name`: @TODO.

* `ffmpeg_media_conversion_input_args`: @TODO.

### Publish

Used by `publish.py` and `approve.py`.

* `scheduler`: @TODO.

* `num_recordings_per_scheduled_batch`: @TODO.

* `enable_twitter`: @TODO.

* `enable_mastodon`: @TODO.

* `require_approval`: @TODO.

* `flag_sensitive_snapshots`: @TODO.

* `show_media_metadata`: @TODO.

* `reply_with_text_to_speech`: @TODO.

* `delete_files_after_upload`: @TODO.

* `api_wait`: @TODO.

* `twitter_api_key`: @TODO.

* `twitter_api_secret`: @TODO.

* `twitter_access_token`: @TODO.

* `twitter_access_token_secret`: @TODO.

* `twitter_max_retries`: @TODO.

* `twitter_retry_wait`: @TODO.

* `twitter_max_status_length`: @TODO.

* `twitter_text_to_speech_segment_duration`: @TODO.

* `twitter_max_text_to_speech_segments`: @TODO.

* `mastodon_instance_url`: @TODO.

* `mastodon_access_token`: @TODO.

* `mastodon_max_retries`: @TODO.

* `mastodon_retry_wait`: @TODO.

* `mastodon_max_status_length`: @TODO.

* `mastodon_max_file_size`: @TODO.

* `mastodon_enable_ffmpeg`: @TODO.

* `mastodon_ffmpeg_output_args`: @TODO.