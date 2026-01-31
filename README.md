## Library to handle Wolf SmartSet communication.

### Features:
- Built-in authentication
- Session creation
- Fetch all devices you have
- Fetch all parameters description
- Fetch value for specific parameter

### Parameter descriptions
Parameters verified only for FGB-28 system.
Other should work.
Keep in mind that core implementation of fetching parameters is removing duplications by value_id and name.

### Token caching
- Authentication responses are stored in `~/.wolf_comm_token_cache.json` so subsequent script runs reuse a cached access token instead of re-authenticating.
- Before every request the client checks if the cached token for the configured username exists and is not expired. If the token is still valid it is reused; when the token is missing or expired the client obtains a fresh token from the Wolf SmartSet login flow and overwrites the cached entry.
- Corrupt cache entries are ignored with warnings, and write failures just log a warning so that missing token files won't crash the client.
