#!/usr/bin/env python3

import subprocess
from argparse import ArgumentParser

from comtypes.client import CreateObject # type: ignore

from record import RecordConfig

if __name__ == '__main__':

	parser = ArgumentParser(description='Lists and exports the voices used by the Microsoft Speech API.')
	parser.add_argument('-list', action='store_true', help='List every voice visible to the Microsoft Speech API.')
	parser.add_argument('-registry', action='store_true', help='Export every installed voice registry key to a .REG file.')
	args = parser.parse_args()

	if not any(vars(args).values()):
		parser.error('No arguments provided.')

	config = RecordConfig()

	# The voices listed here were previously installed from voices packages, some of which
	# can be downloaded via the Windows Speech settings. Note, however, that some voices are
	# not detected properly and require some registry changes to make them visible to the API.
	#
	# The following registry key lists all installed voices:
	# - HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens
	# While the following one lists the voices that the API sees:
	# - HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens
	#
	# If a voice you want is missing, you must copy its registry structure from the first key
	# to the second one. An easy way to do this is to export the first key using the Registry
	# Editor, change "Speech_OneCore" to "Speech", and then import them using the same tool.

	if args.list:
		
		engine = CreateObject('SAPI.SpVoice')
		voice_list = list(engine.GetVoices())
		voice_list.sort(key=lambda x: (x.GetAttribute('Language'), x.GetAttribute('Name')))

		print('Voices visible to the Microsoft Speech API (Name / Language / Gender / Age / Vendor / Description):')

		for i, voice in enumerate(voice_list):
			
			name = voice.GetAttribute('Name')
			language = voice.GetAttribute('Language')
			gender = voice.GetAttribute('Gender')
			age = voice.GetAttribute('Age')
			vendor = voice.GetAttribute('Vendor')
			description = voice.GetDescription()
			
			print(f'[{i+1} of {len(voice_list)}] {name} / {language} / {gender} / {age} / {vendor} / {description}')

		print()

		if config.text_to_speech_default_voice is not None:
			config.text_to_speech_language_voices['Default'] = config.text_to_speech_default_voice

		for language, name in config.text_to_speech_language_voices.items():
			voice = next((voice for voice in voice_list if name.lower() in voice.GetAttribute('Name').lower()), None)
			if voice is None:
				language = config.language_names.get(language, language)
				print(f'Could not find the voice "{name}" ({language}) specified in the configuration file.')

	if args.registry:
		
		try:
			filename = 'voices.reg'
			subprocess.run(['reg', 'export', 'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Speech_OneCore\\Voices\\Tokens', filename, '/y'], check=True, text=True, capture_output=True)

			with open(filename, encoding='utf-16') as file:
				content = file.read()
			
			content = content.replace('Speech_OneCore', 'Speech')
			
			with open(filename, 'w', encoding='utf-16') as file:
				file.write(content)

			if args.list:
				print()

			print(f'Exported the installed voices registry keys to "{filename}".')

		except subprocess.CalledProcessError as error:
			print(f'Failed to export the installed voices registry keys with the error: "{error.stderr}"')