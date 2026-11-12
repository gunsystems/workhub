import io,warnings,uuid
from django import forms
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods,require_GET
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.base import ContentFile
from django.http import HttpResponse,HttpResponseNotAllowed,Http404
from django.shortcuts import render,get_object_or_404,redirect
from tasks.models import Employee,StaffEmployment,EmployeePhoto
from tasks.permissions import profile
from tasks.drive_storage import DriveClient,RepositoryError,configured
from tasks.audit import write


def target_employee(user,employee_id=None):
    if employee_id is not None:
        if not user.is_superuser:raise PermissionDenied
        return get_object_or_404(Employee.objects.select_related('department','user'),pk=employee_id)
    return profile(user)

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

@login_required
@never_cache
@require_http_methods(['GET','POST'])
def my_information(request,employee_id=None):
    employee=target_employee(request.user,employee_id)
    form=PhotoForm();notice=''
    if request.method=='POST':
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
                EmployeePhoto.objects.create(employee=employee,file=stored,uploader=request.user)
            except RepositoryError as exc:form.add_error(None,str(exc))
            else:
                write('update','EmployeePhoto',employee.pk,'사원카드 사진',after={'사진 표시':True},actor=request.user)
                return redirect(request.path+'?photo=saved')
    if request.GET.get('photo')=='saved':notice='사원카드 사진을 변경했습니다.'
    employment=StaffEmployment.objects.filter(employee=employee).first() if employee else None
    photo=EmployeePhoto.objects.filter(employee=employee).order_by('-pk').first() if employee else None
    card=None
    if employee:
        card={'name':employee.name,'department':employee.department.name,
              'job_title':employment.job_title if employment and employment.job_title else employee.get_position_display(),
              'staff_number':employment.staff_number if employment and employment.staff_number else '사번 등록 전'}
    return render(request,'accounts/my_information.html',{'employee':employee,'employment':employment,'card':card,'admin_preview':employee_id is not None,'photo':photo,'photo_form':form,'photo_notice':notice})

@login_required
@never_cache
@require_GET
def card_photo(request,employee_id):
    employee=get_object_or_404(Employee,pk=employee_id)
    own=profile(request.user)
    if not request.user.is_superuser and (not own or own.pk!=employee.pk):raise PermissionDenied
    photo=EmployeePhoto.objects.filter(employee=employee).order_by('-pk').first()
    if not photo or not photo.file:raise Http404
    try:
        with photo.file.open('rb') as source:data=source.read(5*1024*1024+1)
        if len(data)>5*1024*1024:raise Http404
    except (FileNotFoundError,RepositoryError):return HttpResponse('사진을 불러오지 못했습니다.',status=503)
    response=HttpResponse(data,content_type='image/jpeg')
    response['X-Content-Type-Options']='nosniff';response['Content-Security-Policy']="default-src 'none'; frame-ancestors 'self'"
    return response
