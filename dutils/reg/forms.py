# imports # {{{ 
from django import forms
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.contrib.auth.forms import PasswordChangeForm as DPCF

from dutils.utils import RequestForm, log_user_in
# }}} 

# LoginForm # {{{
class LoginForm(RequestForm):
    """
    Base class for authenticating users. Extend this to get a form that accepts
    username/password logins.

    Example usage:
    --------------
        url(r'^login/$',
           "dutils.utils.form_handler",
           {
               'template': 'registration/login.html',
               "form_cls": "dutils.utils.LoginForm",
               "next": "/",
           },
           name='auth_login'
        ),
    """
    username = forms.CharField(label=_("Username"), max_length=30)
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput)

    def clean_password(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')

        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            try:
                user = User.objects.filter(email__iexact=username)[0]
            except IndexError:
                pass
            else:
                username = user.username

        if not user.check_password(password):
            raise forms.ValidationError(_("Please enter a correct username and password. Note that both fields are case-sensitive."))

        self.user_cache = user

        return password

    def get_user_id(self):
        if self.user_cache:
            return self.user_cache.id
        return None

    def get_user(self):
        return self.user_cache

    def save(self):
        log_user_in(self.user_cache, self.request)
        return "/"
# }}}

class PasswordChangeForm(DPCF):
    old_password = forms.CharField(
        label=_("Old password"), widget=forms.PasswordInput,
    )
    new_password1 = forms.CharField(
        label=_("New password"), widget=forms.PasswordInput, min_length=8
    )
    new_password2 = forms.CharField(
        label=_("New password confirmation"), widget=forms.PasswordInput,
        min_length=8
    )


