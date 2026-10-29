"""Offline regressions for the reviewed runtime dependency security fixes."""
from __future__ import annotations

import ssl

import certifi
import jwt
import pytest


def test_runtime_tls_bundle_excludes_compromised_emsign_root():
    """Do not trust the root affected by GHSA-6q48-6c7w-9gw2."""
    context = ssl.create_default_context(cafile=certifi.where())
    common_names = {
        value
        for certificate in context.get_ca_certs()
        for relative_name in certificate["subject"]
        for name, value in relative_name
        if name == "commonName"
    }
    assert common_names, "the runtime TLS bundle must contain trust anchors"
    assert "emSign ECC Root CA - G3" not in common_names


def test_jwk_cannot_supply_empty_hmac_verification_key():
    """An empty oct JWK must fail before token verification."""
    with pytest.raises(jwt.exceptions.InvalidKeyError):
        jwt.PyJWK.from_dict({"kty": "oct", "k": "", "alg": "HS256"})
