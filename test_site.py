#!/usr/bin/env python3
"""
Тест создания простого сайта через API
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_create_site():
    print("🌐 Тестирование создания простого сайта через API...\n")
    
    # 1. Регистрация/вход
    print("1️⃣ Аутентификация...")
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
        if response.status_code != 200:
            # Попробуем войти
            response = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={
                    "email": test_email,
                    "password": "testpass123"
                },
                timeout=10
            )
        
        if response.status_code == 200:
            token = response.json().get("access_token")
            print(f"   ✅ Токен получен\n")
        else:
            print(f"   ❌ Ошибка аутентификации: {response.status_code}")
            return
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. Создание проекта
    print("2️⃣ Создание проекта...")
    try:
        response = requests.post(
            f"{BASE_URL}/api/projects",
            json={"name": "Simple Site Test"},
            headers=headers,
            timeout=30
        )
        if response.status_code == 200:
            project_data = response.json()
            project_id = project_data.get("project_id")
            print(f"   ✅ Проект создан: {project_id}\n")
        else:
            print(f"   ❌ Ошибка: {response.status_code}")
            return
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return
    
    # 3. Запрос на создание простого сайта
    print("3️⃣ Отправка запроса агенту: 'Создай простой сайт'...")
    print("   ⏳ Ожидание ответа (это может занять 1-3 минуты)...\n")
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/chat",
            json={
                "project_id": project_id,
                "message": "Создай простой сайт"
            },
            headers=headers,
            timeout=300  # 5 минут
        )
        
        if response.status_code == 200:
            result = response.json()
            print("=" * 60)
            print("✅ ЗАПРОС ВЫПОЛНЕН УСПЕШНО!")
            print("=" * 60)
            
            # Ответ агента
            agent_response = result.get('response', '')
            print(f"\n📝 Ответ агента:\n{agent_response}\n")
            
            # Статистика шагов
            steps = result.get('steps', [])
            print(f"📊 Статистика:")
            print(f"   • Всего шагов: {len(steps)}")
            
            # Подсчет типов шагов
            step_types = {}
            for step in steps:
                step_type = step.get('type', 'unknown')
                step_types[step_type] = step_types.get(step_type, 0) + 1
            
            print(f"   • Типы шагов:")
            for stype, count in step_types.items():
                print(f"     - {stype}: {count}")
            
            # Проверка на ошибки
            errors = [s for s in steps if s.get('type') == 'error' or 'Ошибка' in s.get('content', '')]
            if errors:
                print(f"\n⚠️ Обнаружено ошибок: {len(errors)}")
                for err in errors[:3]:
                    print(f"   ❌ {err.get('content', '')[:150]}")
            else:
                print(f"\n✅ Ошибок не обнаружено")
            
            # Показываем ключевые шаги
            print(f"\n📋 Ключевые шаги выполнения:")
            important_steps = [s for s in steps if s.get('type') in ['tool_call', 'tool_result']]
            for i, step in enumerate(important_steps[:10], 1):
                step_type = step.get('type', 'unknown')
                content = step.get('content', '')
                tool_name = step.get('tool_name', '')
                
                if step_type == 'tool_call':
                    print(f"   {i}. 🔧 {content}")
                    if tool_name:
                        print(f"      Инструмент: {tool_name}")
                elif step_type == 'tool_result':
                    result_content = content[:100]
                    if '✅' in result_content or 'успешно' in result_content.lower():
                        print(f"   {i}. ✅ {result_content}...")
                    else:
                        print(f"   {i}. 📄 {result_content}...")
            
            # Проверка созданных файлов
            print(f"\n4️⃣ Проверка созданных файлов...")
            try:
                files_response = requests.get(
                    f"{BASE_URL}/api/projects/{project_id}/files?tree=true",
                    headers=headers,
                    timeout=10
                )
                if files_response.status_code == 200:
                    files_data = files_response.json()
                    tree = files_data.get('tree', [])
                    
                    def print_tree(items, indent=0):
                        for item in items:
                            item_type = "📁" if item.get('type') == 'dir' else "📄"
                            print(f"{'  ' * indent}{item_type} {item.get('name', '?')}")
                            if item.get('children'):
                                print_tree(item.get('children', []), indent + 1)
                    
                    if tree:
                        print(f"   📁 Структура проекта:")
                        print_tree(tree)
                    else:
                        files = files_data.get('files', [])
                        print(f"   📁 Файлы ({len(files)}):")
                        for file in files[:10]:
                            print(f"      {'📁' if file.get('type') == 'dir' else '📄'} {file.get('path', '?')}")
            except Exception as e:
                print(f"   ⚠️ Не удалось получить список файлов: {e}")
            
        else:
            print(f"❌ Ошибка: {response.status_code}")
            print(f"   Ответ: {response.text}")
    except requests.exceptions.Timeout:
        print("⏱️ Таймаут! Запрос занял больше 5 минут.")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("✅ Тестирование завершено!")
    print("=" * 60)

if __name__ == "__main__":
    test_create_site()
