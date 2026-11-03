import jwt
import base64
from rest_framework import authentication
from rest_framework import exceptions
from django.conf import settings
from api.models import User

class CustomJWTAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return None

        token = parts[1]
        try:
            # Decode using base64 key since the Java side signed with decoded key bytes
            secret_key_base64 = settings.JWT_SECRET
            try:
                # Decoders.BASE64.decode(secretKey)
                secret_key = base64.b64decode(secret_key_base64)
            except Exception:
                secret_key = secret_key_base64.encode('utf-8')
                
            payload = jwt.decode(token, secret_key, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed('Invalid token')

        email = payload.get('sub')
        if not email:
            raise exceptions.AuthenticationFailed('Invalid token claims')

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed('User not found')

        return (user, token)
