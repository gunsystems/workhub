from django.urls import path
from . import collaboration,collaboration_views,personal
urlpatterns=[
    path('',collaboration_views.official_list,name='official_list'),
    path('new/',collaboration.edit_task,name='official_new'),
    path('<int:pk>/<str:kind>/',collaboration.action,name='official_action'),
    path('notifications/',collaboration_views.notifications,name='work_notifications'),
    path('notifications/<int:pk>/read/',collaboration_views.read_notification,name='read_work_notification'),
    path('personal/',personal.board,name='personal_home'),
    path('personal/boards/<int:pk>/',personal.board,name='personal_board'),
    path('personal/boards/action/',personal.board_action,name='personal_board_action'),
    path('personal/cards/new/',personal.edit_card,name='personal_card_new'),
    path('personal/cards/<int:pk>/',personal.edit_card,name='personal_card_edit'),
    path('personal/cards/<int:pk>/action/',personal.card_action,name='personal_card_action'),
    path('personal/cards/<int:pk>/promote/',personal.promote,name='personal_promote'),
]
