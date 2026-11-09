from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'accounts'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from tasks.audit import register
        register()
        from tasks.supervisors import register as register_supervisors
        register_supervisors()
        # Admin autodiscovery has completed before this app's ready hook.
        from django.contrib import admin
        from django_otp.plugins.otp_totp.models import TOTPDevice
        if admin.site.is_registered(TOTPDevice):
            admin.site.unregister(TOTPDevice)
