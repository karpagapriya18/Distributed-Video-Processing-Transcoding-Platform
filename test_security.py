import time
import pytest
from fastapi import HTTPException
from common.security import media_path, signature, verify_signature


def test_media_signature_is_bound_to_id_kind_and_expiry():
    expires=int(time.time())+60
    token=signature('outputs','abc',expires)
    verify_signature('outputs','abc',expires,token)
    for kind,item,deadline in [('thumbnails','abc',expires),('outputs','other',expires),('outputs','abc',1)]:
        with pytest.raises(HTTPException):
            verify_signature(kind,item,deadline,token)


def test_path_traversal_is_rejected():
    with pytest.raises(HTTPException):
        media_path('../secret')
