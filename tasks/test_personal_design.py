from datetime import date
from django.contrib.auth import get_user_model
from django.test import TestCase,Client
from .models import PersonalBoard,PersonalColumn,PersonalCard


class PersonalDesignTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.owner=U.objects.create_user('design-owner',password='test')
        self.other=U.objects.create_user('design-other',password='test')
        self.admin=U.objects.create_superuser('design-admin',password='test')
        self.board=PersonalBoard.objects.create(owner=self.owner,name='디자인 보드')
        self.todo=PersonalColumn.objects.create(board=self.board,name='할 일',workflow_role='todo')
        self.doing=PersonalColumn.objects.create(board=self.board,name='진행 중',workflow_role='doing',color='blue')
        self.done=PersonalColumn.objects.create(board=self.board,name='완료',workflow_role='done',color='green')
        self.card=PersonalCard.objects.create(column=self.todo,title='중요 점검',priority='urgent',due_date=date(2026,1,31),repeat='monthly')
        self.client.force_login(self.owner)
    def action(self,action,**extra):
        return self.client.post(f'/work/personal/cards/{self.card.pk}/action/',{'action':action,**extra})

    def test_drop_in_done_completes_once_and_repeat_stays_in_todo(self):
        self.action('move',column=self.done.pk)
        self.card.refresh_from_db()
        self.assertEqual(self.card.state,'done')
        self.assertIsNotNone(self.card.completed_at)
        self.assertEqual(self.card.next_occurrence.column,self.todo)
        self.assertEqual(self.card.next_occurrence.priority,'urgent')
        self.action('move',column=self.done.pk)
        self.action('move',column=self.doing.pk)
        self.card.refresh_from_db()
        self.assertEqual(self.card.state,'active')
        self.assertIsNone(self.card.completed_at)
        self.action('move',column=self.done.pk)
        self.assertEqual(PersonalCard.objects.count(),2)

    def test_complete_button_moves_card_and_default_board_shows_done(self):
        self.action('complete')
        self.card.refresh_from_db()
        self.assertEqual(self.card.column,self.done)
        response=self.client.get(f'/work/personal/boards/{self.board.pk}/')
        self.assertContains(response,'card-completed')
        self.assertContains(response,'중요 점검')

    def test_edit_move_also_completes_without_reopening_on_content_edit(self):
        data={'title':self.card.title,'column':self.done.pk,'priority':'high','due_date':'2026-01-31','repeat':'monthly','items':'점검'}
        self.assertEqual(self.client.post(f'/work/personal/cards/{self.card.pk}/',data).status_code,302)
        self.card.refresh_from_db(); self.assertEqual(self.card.state,'done')
        data['title']='정정된 제목'
        self.client.post(f'/work/personal/cards/{self.card.pk}/',data)
        self.card.refresh_from_db(); self.assertEqual(self.card.state,'done')
        self.assertEqual(PersonalCard.objects.count(),2)

    def test_color_and_semantics_settings_owned_validated_and_do_not_bulk_complete(self):
        data={'action':'configure_column','board':self.board.pk,'column':self.todo.pk,'name':'점검 완료','color':'purple','workflow_role':'done'}
        self.assertEqual(self.client.post('/work/personal/boards/action/',data).status_code,302)
        self.todo.refresh_from_db(); self.card.refresh_from_db()
        self.assertEqual(self.todo.color,'purple');self.assertEqual(self.card.state,'active')
        data['color']='invalid-color'
        self.assertEqual(self.client.post('/work/personal/boards/action/',data).status_code,200)
        self.todo.refresh_from_db();self.assertEqual(self.todo.color,'purple')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post('/work/personal/boards/action/',{**data,'color':'green'}).status_code,404)

    def test_priority_rejects_invalid_values_and_card_menu_is_compact(self):
        response=self.client.post(f'/work/personal/cards/{self.card.pk}/',{'title':'제목','column':self.todo.pk,'priority':'invalid','due_date':'2026-01-31','repeat':'monthly'})
        self.assertEqual(response.status_code,200)
        self.card.refresh_from_db();self.assertEqual(self.card.priority,'urgent')
        response=self.client.get(f'/work/personal/boards/{self.board.pk}/')
        for text in ['priority-urgent','긴급','card-menu','column-green','보드 전체']:
            self.assertContains(response,text)

    def test_grouped_navigation_active_state_and_permissions(self):
        response=self.client.get(f'/work/personal/boards/{self.board.pk}/')
        for text in ['내 공간','업무 관리','알림','aria-current="page"','nav-toggle']:
            self.assertContains(response,text)
        self.assertNotContains(response,'관리자 전용')
        self.client.force_login(self.admin)
        response=self.client.get('/work/')
        self.assertContains(response,'조직 현황')
        self.assertContains(response,'관리자 전용')
        self.assertContains(response,'nav-collapsible')
        self.assertNotContains(response,'중요 점검')

    def test_configure_and_completion_require_csrf(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        self.assertEqual(client.post('/work/personal/boards/action/',{'action':'configure_column','board':self.board.pk,'column':self.todo.pk}).status_code,403)
        self.assertEqual(client.post(f'/work/personal/cards/{self.card.pk}/action/',{'action':'move','column':self.done.pk}).status_code,403)
