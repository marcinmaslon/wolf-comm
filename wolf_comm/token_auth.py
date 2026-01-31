from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from httpx import AsyncClient

from wolf_comm import constants

from lxml import html
import pkce
import shortuuid



_LOGGER = logging.getLogger(__name__)
_TOKEN_CACHE_FILE = Path.cwd() / ".wolf_comm_token_cache.json"


class Tokens:
    """Has only one token: access"""

    def __init__(self, access_token: str, expires_in: int):
        self.access_token = access_token
        self.expire_date = datetime.datetime.now() + datetime.timedelta(seconds=expires_in)

    def is_expired(self) -> bool:
        return self.expire_date < datetime.datetime.now()

    def to_cache_entry(self) -> dict:
        return {
            "access_token": self.access_token,
            "expire_date": self.expire_date.isoformat(),
        }

    @classmethod
    def from_cache_entry(cls, entry: dict) -> "Tokens":
        expire_date = datetime.datetime.fromisoformat(entry["expire_date"])
        instance = cls.__new__(cls)
        instance.access_token = entry["access_token"]
        instance.expire_date = expire_date
        return instance


class TokenAuth:
    """Adds poosibility to login with passed credentials and cache tokens locally."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

    async def token(self, client: AsyncClient) -> Tokens:
        cached = self._load_cached_tokens()
        if cached:
            if not cached.is_expired():
                print("Using cached token for user %s", self.username)
                return cached
            _LOGGER.info("Cached token for user %s expired, requesting a new one", self.username)
        try:
            # Generate client-sided variables for OpenID
            code_verifier, code_challenge = pkce.generate_pkce_pair()
            state = shortuuid.uuid()
        

            # Retrieve verification token from WOLF website
            r = await client.get(constants.AUTHENTICATION_BASE_URL + '/Account/Login?ReturnUrl=/idsrv/connect/authorize/callback?client_id={}&redirect_uri={}/signin-callback.html&response_type=code&scope=openid%2520profile api role&state={}&code_challenge={}&code_challenge_method=S256&response_mode=query&lang=de-DE'.format(constants.AUTHENTICATION_CLIENT, constants.BASE_URL,state, code_challenge))

            _LOGGER.debug('Verification code response: %s', r.content)

            tree = html.document_fromstring(r.text)
            elements = tree.xpath('//form/input/@value')

            if elements:

                _LOGGER.debug('Verification token: %s', elements[0])

                verification_token = elements[0] # __RequestVerificationToken

                # Get code
                login_data = {
                    "Input.Username": self.username,
                    "Input.Password": self.password,
                    "__RequestVerificationToken": verification_token
                }

                r = await client.post(
                    constants.AUTHENTICATION_BASE_URL + "/Account/Login",
                    params={
                        "ReturnUrl": constants.AUTHENTICATION_URL + "/connect/authorize/callback?client_id={}&redirect_uri={}/signin-callback.html&response_type=code&scope=openid profile api role&state={}&code_challenge={}&code_challenge_method=S256&response_mode=query&lang=de-DE".format(constants.AUTHENTICATION_CLIENT, constants.BASE_URL, state,code_challenge)
                    },
                    headers={
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                    },
                    data=login_data,
                    cookies = r.cookies,
                    follow_redirects=True
                )
                
                _LOGGER.debug('Code response: %s', r.content)
                code = r.url.params['code']
                

                headers = {
                    "Cache-control": "no-cache",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:108.0) Gecko/20100101 Firefox/108.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "de-DE,de;q=0.8,en-US;q=0.5,en;q=0.3",
                    "Referer": constants.BASE_URL + "/",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                    "TE": "trailers"
                }

                # Get token
                r = await client.post(
                    constants.AUTHENTICATION_BASE_URL + "/connect/token",
                    headers=headers,
                    data={
                        "client_id": "smartset.web",
                        "code": code,
                        "redirect_uri": constants.BASE_URL + "/signin-callback.html",
                        "code_verifier": code_verifier,
                        "grant_type": "authorization_code",
                    },
                )
                        
                token_response = r.json()
                _LOGGER.debug('Token response: %s', token_response)
                if "error" in token_response:
                    raise InvalidAuth
                _LOGGER.info('Successfully authenticated')
                tokens = Tokens(token_response.get("access_token"), token_response.get("expires_in"))
                _LOGGER.info("Caching token that expires at %s", tokens.expire_date.isoformat())
                self._save_cached_tokens(tokens)
                return tokens
            else:
                raise InvalidAuth
        except Exception as e:
            _LOGGER.error('An error occurred: %s', e)
            raise InvalidAuth

    def _load_cached_tokens(self) -> Tokens | None:
        cache = self._read_cache()
        entry = cache.get(self.username)
        if not entry:
            return None
        try:
            print("Loaded cached token entry for %s", self.username)
            return Tokens.from_cache_entry(entry)
        except (KeyError, ValueError) as exc:
            _LOGGER.warning("Invalid cache entry for user %s: %s", self.username, exc)
            return None

    def _save_cached_tokens(self, tokens: Tokens) -> None:
        cache = self._read_cache()
        cache[self.username] = tokens.to_cache_entry()
        try:
            print("Saving cached token entry for %s", self.username)
            _TOKEN_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
        except OSError as exc:
            _LOGGER.warning("Failed to write token cache to %s: %s", _TOKEN_CACHE_FILE, exc)

    def _read_cache(self) -> dict:
        try:
            raw = _TOKEN_CACHE_FILE.read_text(encoding="utf-8")
            print("Read token cache file %s (size %d)", _TOKEN_CACHE_FILE, len(raw))
            return json.loads(raw)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            _LOGGER.warning("Failed to parse token cache at %s: %s", _TOKEN_CACHE_FILE, exc)
            return {}

class InvalidAuth(Exception):
    """Please check whether you entered an invalid username or password. If everything looks fine then probably there is an issue with Wolf SmartSet servers."""
    pass
