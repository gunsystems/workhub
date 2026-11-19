import io,warnings,uuid,hashlib
from django import forms
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods,require_GET
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.base import ContentFile
from django.http import HttpResponse,HttpResponseNotAllowed,Http404
from django.shortcuts import render,get_object_or_404,redirect
from tasks.models import Employee,StaffEmployment,EmployeePhoto,EmployeeAddress
from tasks.permissions import profile
from tasks.drive_storage import DriveClient,RepositoryError,configured
from tasks.audit import write
from django.db import transaction
from django.utils import timezone
import re


def target_employee(user,employee_id=None):
    if employee_id is not None:
        if not user.is_superuser:raise PermissionDenied
        return get_object_or_404(Employee.objects.select_related('department','user'),pk=employee_id)
    return profile(user)

def photo_cache_key(photo):
    identity=f'{photo.employee_id}:{photo.pk}:{photo.created_at.isoformat()}:{photo.file.name}'
    return 'employee-photo:v2:'+hashlib.sha256(identity.encode()).hexdigest()


class PhotoForm(forms.Form):
    photo=forms.FileField(label='사원카드 사진',widget=forms.FileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}))
    def clean_photo(self):
        from PIL import Image,ImageOps,UnidentifiedImageError
        value=self.cleaned_data['photo']
        if not 0<value.size<=5*1024*1024:raise ValidationError('사진은 5MB 이하로 선택하세요.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(value) as image:
                    if image.format not in ['JPEG','PNG','WEBP'] or image.width*image.height>16000000:raise ValueError
                    image=ImageOps.exif_transpose(image)
                    image=ImageOps.fit(image.convert('RGB'),(600,760),method=Image.Resampling.LANCZOS)
                    output=io.BytesIO();image.save(output,format='JPEG',quality=90)
            return ContentFile(output.getvalue(),name='employee-photo-'+uuid.uuid4().hex+'.jpg')
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombWarning,Image.DecompressionBombError):
            raise ValidationError('JPG·PNG·WebP 사진을 선택하세요. 최대 1,600만 화소까지 지원합니다.') from None


class AddressForm(forms.Form):
    postal_code=forms.CharField(label='우편번호',max_length=5,required=False,widget=forms.TextInput(attrs={'inputmode':'numeric','autocomplete':'postal-code','placeholder':'5자리 우편번호'}))
    address=forms.CharField(label='기본 주소',max_length=255,required=False,widget=forms.TextInput(attrs={'autocomplete':'address-line1','placeholder':'도로명 또는 지번 주소'}))
    detail=forms.CharField(label='상세 주소',max_length=255,required=False,widget=forms.TextInput(attrs={'autocomplete':'address-line2','placeholder':'동·호수 등 상세 주소'}))
    revision=forms.IntegerField(min_value=0,widget=forms.HiddenInput)
    def clean_postal_code(self):
        code=self.cleaned_data['postal_code'].strip()
        if code and not re.fullmatch(r'[0-9]{5}',code):raise ValidationError('우편번호는 숫자 5자리로 입력하세요.')
        return code
    def clean(self):
        data=super().clean()
        if (data.get('postal_code') or data.get('detail')) and not data.get('address'):self.add_error('address','기본 주소를 함께 입력하세요.')
        return data

def tenure_text(employment,today=None):
    if not employment:return '입사일 미등록'
    today=today or timezone.localdate()
    if employment.joined_on>today:return '입사 예정'
    end=min(today,employment.ended_on) if employment.ended_on else today
    months=(end.year-employment.joined_on.year)*12+end.month-employment.joined_on.month-(end.day<employment.joined_on.day)
    years,months=divmod(max(0,months),12)
    if not years and not months:return '1개월 미만'
    return ' '.join(x for x in [f'{years}년' if years else '',f'{months}개월' if months else ''] if x)

@login_required
@never_cache
@require_http_methods(['GET','POST'])
def my_information(request,employee_id=None):
    employee=target_employee(request.user,employee_id)
    address=EmployeeAddress.objects.filter(employee=employee).first() if employee else None
    address_form=AddressForm(initial={k:getattr(address,k) if address else (0 if k=='revision' else '') for k in ['postal_code','address','detail','revision']})
    form=PhotoForm();notice='';address_notice='';address_open=False
    if request.method=='POST' and request.POST.get('action')=='address':
        if not employee:raise PermissionDenied
        address_open=True;address_form=AddressForm(request.POST)
        if address_form.is_valid():
            data=address_form.cleaned_data
            with transaction.atomic():
                current,_=EmployeeAddress.objects.get_or_create(employee=employee)
                changed=[name for name in ['postal_code','address','detail'] if getattr(current,name)!=data[name]]
                if current.revision!=data['revision']:
                    address_form.add_error(None,'다른 화면에서 주소가 변경되었습니다. 새로고침 후 최신 주소를 확인해 주세요.')
                elif not changed:return redirect(request.path+'?address=saved')
                else:
                    updated=EmployeeAddress.objects.filter(pk=current.pk,revision=data['revision']).update(**{k:data[k] for k in ['postal_code','address','detail']},revision=current.revision+1,updated_at=timezone.now())
                    if updated:
                        write('update','EmployeeAddress',employee.pk,'직원 개인 주소',after={'수정 항목':[str(EmployeeAddress._meta.get_field(k).verbose_name) for k in changed]},actor=request.user)
                        return redirect(request.path+'?address=saved')
                    address_form.add_error(None,'주소가 변경되었습니다. 새로고침 후 다시 확인해 주세요.')
    if request.method=='POST' and request.POST.get('action')!='address':
        if request.POST.get('action') not in ['photo','remove_photo']:return HttpResponseNotAllowed(['GET'])
        if not employee:raise PermissionDenied
        if request.POST['action']=='remove_photo':
            EmployeePhoto.objects.create(employee=employee,uploader=request.user)
            write('update','EmployeePhoto',employee.pk,'사원카드 사진',after={'사진 표시':False},actor=request.user)
            return redirect(request.path+'?photo=saved')
        form=PhotoForm(request.POST,request.FILES)
        if form.is_valid():
            try:
                if not configured():raise RepositoryError('사진 저장소가 준비되지 않았습니다. 관리자에게 문의하세요.')
                normalized=form.cleaned_data['photo'];stored=DriveClient().upload(normalized.name,normalized)
                saved_photo=EmployeePhoto.objects.create(employee=employee,file=stored,uploader=request.user)
                from django.core.cache import cache
                try:
                    normalized.seek(0);cache.set(photo_cache_key(saved_photo),normalized.read(),timeout=3600)
                except Exception:pass
            except RepositoryError as exc:form.add_error(None,str(exc))
            else:
                write('update','EmployeePhoto',employee.pk,'사원카드 사진',after={'사진 표시':True},actor=request.user)
                return redirect(request.path+'?photo=saved')
    if request.GET.get('photo')=='saved':notice='사원카드 사진을 변경했습니다.'
    if request.GET.get('address')=='saved':address_notice='주소를 저장했습니다.'
    employment=StaffEmployment.objects.filter(employee=employee).first() if employee else None
    photo=EmployeePhoto.objects.filter(employee=employee).order_by('-pk').first() if employee else None
    card=None
    if employee:
        card={'name':employee.name,'department':employee.department.name,
              'job_title':employment.job_title if employment and employment.job_title else employee.get_position_display(),
              'staff_number':employment.staff_number if employment and employment.staff_number else '사번 등록 전'}
    return render(request,'accounts/my_information.html',{'employee':employee,'employment':employment,'card':card,'admin_preview':employee_id is not None,'photo':photo,'photo_form':form,'photo_notice':notice,'address':address,'address_form':address_form,'address_notice':address_notice,'address_open':address_open,'tenure':tenure_text(employment)})

@login_required
@never_cache
@require_GET
def card_photo(request,employee_id):
    employee=get_object_or_404(Employee,pk=employee_id)
    own=profile(request.user)
    if not request.user.is_superuser and (not own or own.pk!=employee.pk):raise PermissionDenied
    photo=EmployeePhoto.objects.filter(employee=employee).order_by('-pk').first()
    if not photo or not photo.file:raise Http404
    # Authorize first, then cache the immutable photo revision. Never cache a page/ACL.
    from django.core.cache import cache
    key=photo_cache_key(photo)
    try:
        try:data=cache.get(key)
        except Exception:data=None
        if data is None:
            with photo.file.open('rb') as source:data=source.read(5*1024*1024+1)
            if not data or len(data)>5*1024*1024:raise Http404
            try:cache.set(key,data,timeout=3600)
            except Exception:pass
    except (FileNotFoundError,RepositoryError):return HttpResponse('사진을 불러오지 못했습니다.',status=503)
    response=HttpResponse(data,content_type='image/jpeg')
    response['X-Content-Type-Options']='nosniff';response['Content-Security-Policy']="default-src 'none'; frame-ancestors 'self'"
    return response
