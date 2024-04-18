#!/usr/bin/env python3

from time import sleep

from limits import RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from .config import config

class RateLimiter:
	""" A rate limiter wrapper that restricts the number of requests made to the Wayback Machine and its APIs. """

	wayback_machine_memory_storage: MemoryStorage
	wayback_machine_rate_limiter: MovingWindowRateLimiter
	wayback_machine_requests_per_minute: RateLimitItemPerSecond

	cdx_api_memory_storage: MemoryStorage
	cdx_api_rate_limiter: MovingWindowRateLimiter
	cdx_api_requests_per_second: RateLimitItemPerSecond

	save_api_memory_storage: MemoryStorage
	save_api_rate_limiter: MovingWindowRateLimiter
	save_api_requests_per_second: RateLimitItemPerSecond

	def __init__(self):

		self.wayback_machine_memory_storage = MemoryStorage()
		self.wayback_machine_rate_limiter = MovingWindowRateLimiter(self.wayback_machine_memory_storage)
		self.wayback_machine_requests_per_minute = RateLimitItemPerSecond(config.wayback_machine_rate_limit_amount, config.wayback_machine_rate_limit_window)

		self.cdx_api_memory_storage = MemoryStorage()
		self.cdx_api_rate_limiter = MovingWindowRateLimiter(self.cdx_api_memory_storage)
		self.cdx_api_requests_per_second = RateLimitItemPerSecond(config.cdx_api_rate_limit_amount, config.cdx_api_rate_limit_window)

		self.save_api_memory_storage = MemoryStorage()
		self.save_api_rate_limiter = MovingWindowRateLimiter(self.save_api_memory_storage)
		self.save_api_requests_per_second = RateLimitItemPerSecond(config.save_api_rate_limit_amount, config.save_api_rate_limit_window)

	def wait_for_wayback_machine_rate_limit(self, **kwargs) -> None:
		""" Waits for a given amount of time if the user-defined Wayback Machine rate limit has been reached. Otherwise, returns immediately. Thread-safe. """
		while not self.wayback_machine_rate_limiter.hit(self.wayback_machine_requests_per_minute, **kwargs):
			sleep(config.rate_limit_poll_frequency)

	def wait_for_cdx_api_rate_limit(self, **kwargs) -> None:
		""" Waits for a given amount of time if the user-defined CDX API rate limit has been reached. Otherwise, returns immediately. Thread-safe. """
		while not self.cdx_api_rate_limiter.hit(self.cdx_api_requests_per_second, **kwargs):
			sleep(config.rate_limit_poll_frequency)

	def wait_for_save_api_rate_limit(self, **kwargs) -> None:
		""" Waits for a given amount of time if the user-defined Save API rate limit has been reached. Otherwise, returns immediately. Thread-safe. """
		while not self.save_api_rate_limiter.hit(self.save_api_requests_per_second, **kwargs):
			sleep(config.rate_limit_poll_frequency)

# Note that different scripts use different global rate limiter instances.
# They're only the same between a script and this module.
global_rate_limiter = RateLimiter()