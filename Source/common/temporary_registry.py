#!/usr/bin/env python3

import winreg
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional, Union
from winreg import (
	CreateKeyEx, DeleteKey, DeleteValue, EnumKey, EnumValue,
	OpenKey, QueryInfoKey, QueryValueEx, SetValueEx,
)

from .logger import log

class TemporaryRegistry:
	""" A temporary registry that remembers and undos any changes (key additions and deletions) made to the Windows registry. """

	# For the sake of convenience, this class mostly deals with registry key values and forces all queries to look at the
	# 32-bit view of the registry in both 32 and 64-bit applications. Although key values are the main focus, we do keep
	# track of any keys to delete since setting a value may require creating any missing intermediate keys.
	#
	# Focusing only on 32-bit applications makes sense since we're configuring old web plugins. Depending on the registry
	# key, Windows will redirect a query to a different location. For example, writing a value to the registry key
	# "HKEY_CLASSES_ROOT\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}" will store the value in the following keys:
	#
	# - "HKEY_CLASSES_ROOT\WOW6432Node\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}"
	# - "HKEY_LOCAL_MACHINE\SOFTWARE\Classes\WOW6432Node\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}"
	# - "HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Classes\CLSID\{06646731-BCF3-11D0-9518-00C04FC2DD79}"
	#
	# See:
	# - https://docs.microsoft.com/en-us/windows/win32/winprog64/registry-reflection
	# - https://docs.microsoft.com/en-us/windows/win32/winprog64/accessing-an-alternate-registry-view

	original_state: dict[tuple[int, str, str], tuple[Optional[int], Any]]
	keys_to_delete: set[tuple[int, str, str]]
	key_paths_to_delete: set[tuple[int, str]]

	OPEN_HKEYS = {
		'hkey_classes_root': winreg.HKEY_CLASSES_ROOT,
		'hkey_current_user': winreg.HKEY_CURRENT_USER,
		'hkey_local_machine': winreg.HKEY_LOCAL_MACHINE,
		'hkey_users': winreg.HKEY_USERS,
		'hkey_performance_data': winreg.HKEY_PERFORMANCE_DATA,
		'hkey_current_config': winreg.HKEY_CURRENT_CONFIG,
		'hkey_dyn_data': winreg.HKEY_DYN_DATA,
	}

	def __init__(self):
		self.original_state = {}
		self.keys_to_delete = set()
		self.key_paths_to_delete = set()

	@staticmethod
	def partition_key(key: str) -> tuple[int, str, str]:
		""" Separates a registry key string into its hkey, key path, and sub key components. """

		first_key, _, key_path = key.partition('\\')
		key_path, _, sub_key = key_path.rpartition('\\')

		first_key = first_key.lower()
		if first_key not in TemporaryRegistry.OPEN_HKEYS:
			raise KeyError(f'The registry key "{key}" does not start with a valid HKEY.')

		hkey = TemporaryRegistry.OPEN_HKEYS[first_key]
		return (hkey, key_path, sub_key)

	def get(self, key: str) -> Any:
		""" Gets the value of a registry key. Returns None if the key doesn't exist. """

		try:
			hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
			with OpenKey(hkey, key_path, access=winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key_handle:
				value, _ = QueryValueEx(key_handle, sub_key)
		except OSError:
			value = None

		return value

	def set(self, key: str, value: Union[int, str, Path], type_: Optional[int] = None) -> Any:
		""" Sets the value of a registry key. Any missing intermediate keys are automatically created. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)

		if type_ is None:
			if isinstance(value, int):
				type_ = winreg.REG_DWORD
			elif isinstance(value, str):
				type_ = winreg.REG_SZ
			elif isinstance(value, Path):
				type_ = winreg.REG_SZ
				value = str(value)
			else:
				raise ValueError(f'The type of the value "{value}" could not be autodetected for the registry key "{key}".')

		if (hkey, key_path) not in self.key_paths_to_delete:

			intermediate_keys = key_path.split('\\')
			while len(intermediate_keys) > 1:

				try:
					intermediate_full_key_path = '\\'.join(intermediate_keys)
					with OpenKey(hkey, intermediate_full_key_path) as key_handle:
						sub_key_exists = True
				except OSError:
					sub_key_exists = False

				intermediate_sub_key = intermediate_keys.pop()
				intermediate_key_path = '\\'.join(intermediate_keys)

				if sub_key_exists:
					break
				else:
					self.keys_to_delete.add((hkey, intermediate_key_path, intermediate_sub_key))

			self.key_paths_to_delete.add((hkey, key_path))

		original_state_key = (hkey, key_path, sub_key)
		original_state_value: tuple[Optional[int], Any]

		with CreateKeyEx(hkey, key_path, access=winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_32KEY) as key_handle:
			try:
				original_value, original_type = QueryValueEx(key_handle, sub_key)
				original_state_value = (original_type, original_value)
				result = original_value
			except OSError:
				original_state_value = (None, None)
				result = None

			SetValueEx(key_handle, sub_key, 0, type_, value)

		if original_state_key not in self.original_state:
			self.original_state[original_state_key] = original_state_value

		return result

	def delete(self, key: str) -> tuple[bool, Any]:
		""" Removes a value from a registry key. Returns true and its data if it existed, otherwise false and None. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)

		try:
			with OpenKey(hkey, key_path, access=winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_32KEY) as key_handle:
				original_value, original_type = QueryValueEx(key_handle, sub_key)
				DeleteValue(key_handle, sub_key)

			original_state_key = (hkey, key_path, sub_key)
			original_state_value = (original_type, original_value)

			if original_state_key not in self.original_state:
				self.original_state[original_state_key] = original_state_value

			success = True
			result = original_value
		except OSError as error:
			log.error(f'Failed to delete the value "{key}" with the error: {repr(error)}')
			success = False
			result = None

		return success, result

	def clear(self, key: str) -> None:
		""" Deletes every value in a registry key. Does not modify its subkeys or their values. """

		key_list = [key for key, _, _ in TemporaryRegistry.traverse(key)]
		for key in key_list:
			self.delete(key)

	@staticmethod
	def traverse(key: str, recursive: bool = False) -> Iterator[tuple[str, Any, int]]:
		""" Iterates over the values of a registry key and optionally its subkeys. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
		key_path += '\\' + sub_key

		try:
			with OpenKey(hkey, key_path, access=winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key_handle:

				num_keys, num_values, _ = QueryInfoKey(key_handle)

				for i in range(num_values):
					try:
						name, data, type_ = EnumValue(key_handle, i)
						yield key + '\\' + name, data, type_
					except OSError as error:
						log.error(f'Failed to enumerate value {i+1} of {num_values} in the registry key "{key}" with the error: {repr(error)}')

				if recursive:

					for i in range(num_keys):
						try:
							child_sub_key = EnumKey(key_handle, i)
							yield from TemporaryRegistry.traverse(key + '\\' + child_sub_key, recursive=recursive)
						except OSError as error:
							log.error(f'Failed to enumerate subkey {i+1} of {num_keys} in the registry key "{key}" with the error: {repr(error)}')

		except OSError as error:
			log.error(f'Failed to traverse the registry key "{key}" with the error: {repr(error)}')

	@staticmethod
	def delete_key_tree(key: str) -> None:
		""" Deletes a registry key and all of its subkeys. """

		hkey, key_path, sub_key = TemporaryRegistry.partition_key(key)
		key_path += '\\' + sub_key

		try:
			with OpenKey(hkey, key_path, access=winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_32KEY) as key_handle:

				num_keys, _, _ = QueryInfoKey(key_handle)

				for i in range(num_keys):
					try:
						child_sub_key = EnumKey(key_handle, i)
						TemporaryRegistry.delete_key_tree(key + '\\' + child_sub_key)
					except OSError as error:
						log.error(f'Failed to enumerate subkey {i+1} of {num_keys} in the registry key "{key}" with the error: {repr(error)}')

				try:
					# Delete self.
					DeleteKey(key_handle, '')
				except OSError as error:
					log.error(f'Failed to delete the registry key "{key}" with the error: {repr(error)}')

		except OSError as error:
			log.error(f'Failed to delete the registry key tree "{key}" with the error: {repr(error)}')

	def restore(self) -> None:
		""" Restores the Windows registry to its original state by undoing any changes, additions, and deletions. """

		for (hkey, key_path, sub_key), (type_, value) in self.original_state.items():
			try:
				with OpenKey(hkey, key_path, access=winreg.KEY_WRITE | winreg.KEY_WOW64_32KEY) as key_handle:
					if type_ is None:
						DeleteValue(key_handle, sub_key)
					else:
						SetValueEx(key_handle, sub_key, 0, type_, value)
			except OSError as error:
				log.error(f'Failed to restore the original value "{value}" type {type_} of the registry key "{hkey}\\{key_path}\\{sub_key}" with the error: {repr(error)}')

		keys_to_delete = sorted(self.keys_to_delete, key=lambda x: len(x[1]), reverse=True)
		for (hkey, key_path, sub_key) in keys_to_delete:
			try:
				with OpenKey(hkey, key_path, access=winreg.KEY_WRITE | winreg.KEY_WOW64_32KEY) as key_handle:
					DeleteKey(key_handle, sub_key)
			except OSError as error:
				log.error(f'Failed to delete the registry key "{hkey}\\{key_path}\\{sub_key}" with the error: {repr(error)}')

		self.original_state = {}
		self.keys_to_delete = set()
		self.key_paths_to_delete = set()

	def __enter__(self):
		return self

	def __exit__(self, exception_type, exception_value, traceback):
		self.restore()