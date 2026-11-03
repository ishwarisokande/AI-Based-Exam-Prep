from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models

class CustomUserManager(BaseUserManager):
    def create_user(self, email, name, password=None, role='USER', **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, name=name, role=role, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password=None, **extra_fields):
        user = self.create_user(email, name, password, role='ADMIN', **extra_fields)
        user.save(using=self._db)
        return user

class User(AbstractBaseUser):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True, max_length=255)
    role = models.CharField(max_length=50, default='USER')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    class Meta:
        db_table = 'users'

class StudyMaterial(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column='user_id', related_name='materials')
    title = models.CharField(max_length=255)
    content_text = models.TextField(db_column='content_text', blank=True, null=True)
    type = models.CharField(max_length=50)  # PDF, YOUTUBE, IMAGE, TEXT
    uploaded_at = models.DateTimeField(auto_now_add=True, db_column='uploaded_at')

    class Meta:
        db_table = 'study_materials'

class GeneratedMCQ(models.Model):
    material = models.ForeignKey(StudyMaterial, on_delete=models.CASCADE, db_column='material_id', related_name='mcqs')
    question = models.CharField(max_length=1000)
    option_a = models.CharField(max_length=500, db_column='optiona')
    option_b = models.CharField(max_length=500, db_column='optionb')
    option_c = models.CharField(max_length=500, db_column='optionc')
    option_d = models.CharField(max_length=500, db_column='optiond')
    correct_answer = models.CharField(max_length=50, db_column='correct_answer')
    difficulty = models.CharField(max_length=50)  # EASY, MEDIUM, HARD

    class Meta:
        db_table = 'generated_mcqs'

class GeneratedNotes(models.Model):
    material = models.OneToOneField(StudyMaterial, on_delete=models.CASCADE, db_column='material_id', related_name='notes')
    notes_text = models.TextField(db_column='notes_text')

    class Meta:
        db_table = 'generated_notes'

class QuizResult(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column='user_id')
    material = models.ForeignKey(StudyMaterial, on_delete=models.SET_NULL, db_column='material_id', null=True, blank=True)
    score = models.IntegerField()
    total = models.IntegerField()
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'quiz_results'
