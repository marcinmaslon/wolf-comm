# Wolf SmartSet Communication Library - AI Coding Agent Instructions

## Architecture Overview

This is a Python library for communicating with Wolf SmartSet Cloud API, providing access to heating system parameters. The library follows a layered architecture:

- **`WolfClient`** (wolf_client.py): Main API client handling authentication, session management, and all communication
- **Parameter Models** (models.py): Type-safe parameter representations with unit-specific classes
- **Session Management** (create_session.py): Handles API session lifecycle
- **Constants** (constants.py): Centralized API endpoint and field name constants

## Key Architectural Patterns

### Parameter Type System
The library uses a sophisticated parameter type hierarchy where each parameter type has its own class:
- `Temperature` → °C unit
- `Pressure` → bar unit  
- `PercentageParameter` → % unit
- `ListItemParameter` → dropdown/enum values
- `SimpleParameter` → untyped parameters

When adding new parameter types, follow the pattern in `models.py` by extending `UnitParameter` or `Parameter`.

### Bundle-Based Parameter Fetching
Parameters are organized into "bundles" for efficient API calls. The `fetch_value()` method groups parameters by `bundle_id` to minimize API requests:
```python
# Groups parameters by bundle_id automatically
bundles = {}
for param in parameters:
    bundles.setdefault(param.bundle_id, []).append(param)
```

### Expert Mode vs Standard Mode
The client supports two modes for parameter discovery:
- **Standard mode**: Uses TabViews structure for organized parameter groups
- **Expert mode**: Extracts all ParameterDescriptors recursively using `_extract_parameter_descriptors()`

## Critical Integration Points

### Authentication Flow
The library uses OAuth2 PKCE flow with Wolf's identity server:
1. `TokenAuth` handles initial authentication
2. `create_session()` establishes API session
3. Session auto-refreshes every 60 seconds via `update_session()`

### Localization System
Parameter names are localized by fetching JavaScript files from Wolf's CDN:
```python
# Fetches localized text based on region
await self.load_localized_json(self.region_set)
# Falls back to English if region not found
```

### Error Handling Strategy
The library implements specific exception hierarchy:
- `FetchFailed` → API read errors
- `ParameterReadError` → Parameter-specific read failures  
- `ParameterWriteError` → Parameter write failures
- `WriteFailed` → API write errors

Always catch these specific exceptions rather than generic HTTP errors.

## Development Workflows

### Adding New Parameter Types
1. Create new class in `models.py` extending `UnitParameter`
2. Add unit constant to `constants.py`
3. Update `_map_parameter()` in `wolf_client.py` to handle the new unit
4. Follow existing pattern with all required properties

### Testing Parameter Discovery
Use the `parameters-examples/` directory JSON files to understand API response structure. These contain real Wolf system responses for:
- `gasparameters.json` → Gas heating systems
- `heatpumpparameter.json` → Heat pump systems  
- `luftung.json` → Ventilation systems

### Package Publishing
The project uses GitHub Actions for automated PyPI publishing on releases. Version is managed in `setup.py`.

## Project-Specific Conventions

### Constants Usage
All API field names and endpoints are centralized in `constants.py`. Always use constants instead of string literals:
```python
# ✅ Correct
data[SESSION_ID] = self.session_id
# ❌ Avoid
data["SessionId"] = self.session_id
```

### Deduplication Logic
The `fetch_parameters()` method implements critical deduplication by `value_id` and `name` to handle Wolf API returning duplicate parameters. This is essential for FGB-28 and other systems.

### Client Flexibility
WolfClient supports both direct httpx.AsyncClient and lambda-based client injection for testing:
```python
# Direct client
WolfClient(username, password, client=httpx.AsyncClient())
# Lambda for dynamic client creation
WolfClient(username, password, client_lambda=lambda: get_client())
```

## Key Dependencies

- **httpx**: Primary HTTP client (async)
- **aiohttp**: Used only for localization file fetching
- **lxml**: XML parsing for authentication responses
- **pkce**: OAuth2 PKCE implementation
- **shortuuid**: Session ID generation

## Common Pitfalls

1. **Session Expiry**: Always check `last_session_refesh` timing - sessions must be refreshed every 60 seconds
2. **Bundle Grouping**: Don't fetch parameters individually; always group by `bundle_id` for efficiency
3. **Localization Fallback**: Ensure graceful fallback to English when regional translations fail
4. **Parameter Deduplication**: Maintain the deduplication logic when modifying parameter fetching
5. **Type Safety**: Use the parameter type hierarchy rather than generic dictionaries

## Domain Knowledge

Wolf SmartSet is a cloud service for Wolf heating systems. Parameter IDs are system-specific but follow patterns (e.g., 18000500001 for boiler sensors). The library is primarily tested with FGB-28 systems but designed to work with other Wolf heating systems.