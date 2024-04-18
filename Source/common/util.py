#!/usr/bin/env python3

import msvcrt
import os
from pathlib import Path
import shutil
import warnings
from typing import Union

from pywinauto.application import ( # type: ignore
	Application as WindowsApplication,
	ProcessNotFoundError as WindowProcessNotFoundError,
	TimeoutError as WindowTimeoutError,
)

from .logger import log

def clamp(value: float, min_value: float, max_value: float) -> float:
	""" Clamps a number between a minimum and maximum value. """
	return max(min_value, min(value, max_value))

def container_to_lowercase(container: Union[list, dict]) -> Union[list, dict]:
	""" Converts the elements of a list or keys of a dictionary to lowercase. """

	if isinstance(container, list):
		return [x.lower() if isinstance(x, str) else x for x in container]
	elif isinstance(container, dict):
		return dict( (key.lower(), value) if isinstance(key, str) else (key, value) for key, value in container.items() )
	else:
		assert False, f'Unhandled container type "{type(container)}".'

def delete_directory(path: Path) -> bool:
	""" Deletes a directory and all of its subdirectories. Does nothing if it doesn't exist. """
	try:
		shutil.rmtree(path)
		success = True
	except OSError:
		success = False
	return success

def delete_file(path: Path) -> bool:
	""" Deletes a file. Does nothing if it doesn't exist. """
	try:
		os.remove(path)
		success = True
	except OSError:
		success = False
	return success

# Ignore the PyWinAuto warning about connecting to a 32-bit executable while using a 64-bit Python environment.
warnings.simplefilter('ignore', category=UserWarning)

def kill_processes_by_path(path: Path) -> None:
	""" Kills all processes running an executable at a given path. """

	try:
		application = WindowsApplication(backend='win32')
		while True:
			application.connect(path=path.absolute(), timeout=5)
			application.kill(soft=False)
	except (WindowProcessNotFoundError, WindowTimeoutError):
		pass
	except Exception as error:
		log.error(f'Failed to kill the processes using the path "{path}" with the error: {repr(error)}')

def kill_process_by_pid(pid: int) -> None:
	""" Kills a process given its PID. """

	try:
		application = WindowsApplication(backend='win32')
		application.connect(process=pid, timeout=5)
		application.kill(soft=False)
	except (WindowProcessNotFoundError, WindowTimeoutError):
		pass
	except Exception as error:
		log.error(f'Failed to kill the process using the PID {pid} with the error: {repr(error)}')

def was_exit_command_entered() -> bool:
	""" Checks if an exit command was typed. Used to stop the execution of scripts that can't use Ctrl-C to terminate. """

	result = False

	if msvcrt.kbhit():
		keys = [msvcrt.getwch()]

		while msvcrt.kbhit():
			keys.append(msvcrt.getwch())

		command = ''.join(keys)

		if 'pause' in command:
			command = input('Paused: ')

		if 'exit' in command:
			result = True

	return result