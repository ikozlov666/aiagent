#!/usr/bin/env python3
"""
Тестовый скрипт для проверки API агента
"""
import requests
import json
import time
import sys

BASE_URL = "http://localhost:8000"

def test_api():
    print("🧪 Начинаю тестирование API...")
    
    # 1. Проверка health
    print("\n1️⃣ Проверка health endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/api/health", timeout=5)
        print(f"   ✅ Health check: {response.status_code}")
        print(f"   📊 Ответ: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return
    
    # 2. Регистрация тестового пользователя
    print("\n2️⃣ Регистрация тестового пользователя...")
    test_email = f"test_{int(time.time())}@test.com"
    test_username = f"testuser_{int(time.time())}"
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "email": test_email,
                "username": test_username,
                "password": "testpass123"
            },
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            token = data.get("access_token")
            print(f"   ✅ Пользователь создан: {test_username}")
            print(f"   🔑 Токен получен: {token[:20]}...")
        else:
            # Попробуем войти, если пользователь уже существует
            print(f"   ⚠️ Регистрация не удалась ({response.status_code}), пробую войти...")
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={
                    "email": test_email,
                    "password": "testpass123"
                },
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                token = data.get("access_token")
                print(f"   ✅ Вход выполнен: {test_username}")
                print(f"   🔑 Токен получен: {token[:20]}...")
            else:
                print(f"   ❌ Ошибка входа: {response.status_code} - {response.text}")
                return
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # 3. Создание проекта
    print("\n3️⃣ Создание тестового проекта...")
    try:
        response = requests.post(
            f"{BASE_URL}/api/projects",
            json={"name": "API Test Project"},
            headers=headers,
            timeout=30
        )
        if response.status_code == 200:
            project_data = response.json()
            project_id = project_data.get("project_id")
            print(f"   ✅ Проект создан: {project_id}")
            print(f"   📊 Данные проекта: {json.dumps(project_data, indent=2, ensure_ascii=False)}")
        else:
            print(f"   ❌ Ошибка создания проекта: {response.status_code} - {response.text}")
            return
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return
    
    # 4. Тест HTTP chat API
    print("\n4️⃣ Тест HTTP chat API (простая задача)...")
    try:
        test_message = "Создай файл test.txt с текстом 'Hello from API test'"
        print(f"   📤 Отправляю сообщение: '{test_message}'")
        
        response = requests.post(
            f"{BASE_URL}/api/chat",
            json={
                "project_id": project_id,
                "message": test_message
            },
            headers=headers,
            timeout=120  # 2 минуты на выполнение
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"   ✅ Запрос выполнен успешно!")
            print(f"   📝 Ответ агента: {result.get('response', '')[:200]}...")
            print(f"   📊 Количество шагов: {len(result.get('steps', []))}")
            
            # Показываем первые несколько шагов
            steps = result.get('steps', [])
            if steps:
                print(f"\n   📋 Первые шаги агента:")
                for i, step in enumerate(steps[:5], 1):
                    step_type = step.get('type', 'unknown')
                    content = step.get('content', '')[:100]
                    print(f"      {i}. [{step_type}] {content}...")
        else:
            print(f"   ❌ Ошибка: {response.status_code} - {response.text}")
    except requests.exceptions.Timeout:
        print(f"   ⏱️ Таймаут (запрос занял больше 2 минут)")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    # 5. Проверка файлов проекта
    print("\n5️⃣ Проверка файлов проекта...")
    try:
        response = requests.get(
            f"{BASE_URL}/api/projects/{project_id}/files",
            headers=headers,
            timeout=10
        )
        if response.status_code == 200:
            files_data = response.json()
            files = files_data.get('files', [])
            print(f"   ✅ Найдено файлов: {len(files)}")
            if files:
                print(f"   📁 Первые файлы:")
                for file in files[:5]:
                    print(f"      - {file.get('path', '?')} ({file.get('type', '?')})")
        else:
            print(f"   ⚠️ Ошибка получения файлов: {response.status_code}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    # 6. Тест более сложной задачи
    print("\n6️⃣ Тест HTTP chat API (сложная задача - создание веб-страницы)...")
    try:
        complex_message = "Создай простую HTML страницу с заголовком 'Тест API' и кнопкой"
        print(f"   📤 Отправляю сообщение: '{complex_message}'")
        
        response = requests.post(
            f"{BASE_URL}/api/chat",
            json={
                "project_id": project_id,
                "message": complex_message
            },
            headers=headers,
            timeout=180  # 3 минуты на выполнение
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"   ✅ Запрос выполнен успешно!")
            print(f"   📝 Ответ агента: {result.get('response', '')[:300]}...")
            print(f"   📊 Количество шагов: {len(result.get('steps', []))}")
            
            # Проверяем, были ли ошибки в шагах
            steps = result.get('steps', [])
            errors = [s for s in steps if s.get('type') == 'error']
            if errors:
                print(f"   ⚠️ Найдено ошибок: {len(errors)}")
                for err in errors:
                    print(f"      ❌ {err.get('content', '')[:200]}")
            else:
                print(f"   ✅ Ошибок не обнаружено")
        else:
            print(f"   ❌ Ошибка: {response.status_code} - {response.text}")
    except requests.exceptions.Timeout:
        print(f"   ⏱️ Таймаут (запрос занял больше 3 минут)")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    print("\n✅ Тестирование завершено!")

if __name__ == "__main__":
    test_api()
