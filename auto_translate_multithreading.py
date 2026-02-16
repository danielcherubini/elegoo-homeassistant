import os
import re
import json
import time
import random
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock


# Target language configuration (language code: filename)
LANGUAGES = {
    'ar': 'ar.json',          # Arabic
    'cs': 'cs.json',          # Czech
    'da': 'da.json',          # Danish
    'de': 'de.json',          # German
    'el': 'el.json',          # Greek
    # 'en': 'en.json',          # English skip
    'es': 'es.json',          # Spanish
    'fr': 'fr.json',          # French
    'it': 'it.json',          # Italian
    'ja': 'ja.json',          # Japanese
    'ko': 'ko.json',          # Korean
    'nl': 'nl.json',          # Dutch
    'pl': 'pl.json',          # Polish
    'pt': 'pt.json',          # Portuguese
    'ru': 'ru.json',          # Russian
    'sk': 'sk.json',          # Slovak
    'th': 'th.json',          # Thai
    'zh-CN': 'zh-Hans.json',  # Chinese (Simplified)
}


def get_en_json_from_github(fpath):
    # https://github.com/danielcherubini/elegoo-homeassistant/blob/main/custom_components/elegoo_printer/translations/en.json
    try:
        url = f"https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/refs/heads/main/custom_components/elegoo_printer/translations/en.json"
        response = requests.get(url).json()
        if len(response) > 0:
            with open(fpath, 'w', encoding='utf-8', newline="\n") as f:     # newline="\n" -> Linux Style
                json.dump(response, f, ensure_ascii=False, indent=2)
            print(f'Get Translation Flie [en.json] Success.')
            return True
        else:
            print(f'Get Translation Flie [en.json] Failure.')
            return False
    except Exception as e:
        print(f"Get Response Error '{url}' : {e}")
        return False


def Google_Translate(text, target_lang='', source_lang='en'):
    contentStr = text.strip('"')
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={source_lang}&tl={target_lang}&dt=t&q={contentStr}"
    response = requests.get(url)

    # -SessionVariable Session `
    # -UserAgent ([Microsoft.PowerShell.Commands.PSUserAgent]::Chrome) `
    # -Method Get `
    # -ContentType 'application/json'

    json = response.json()
    translation = ''
    for result in json[0]:
        translation += result[0]
        translation = translation.replace("\\n", "\n")
        translation = translation.replace("\u003e", ">")
    return translation


def Bing_Translate(text, target_lang='', source_lang='en'):
    def _get_bing_session():
        session_url = 'https://www.bing.com/translator'
        session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': session_url
        }
        session.headers.update(headers)
        _response = session.get(session_url)
        _pattern = re.compile(r'params_AbusePreventionHelper\s*=\s*(\[.*?\]);', re.DOTALL)
        _match = _pattern.search(_response.text)
        if _match:
            _params = _match.group(1)
            key, token, time = [p.strip('"').replace('[', '').replace(']', '') for p in _params.split(',')]
            session.headers.update({'key': key, 'token': token})
        _match = re.search(r'IG:"(\w+)"', _response.text)
        if _match:
            ig_value = _match.group(1)
            session.headers.update({'IG': ig_value})
        return session
    
    api_url = "https://www.bing.com/ttranslatev3?"
    b_session = _get_bing_session()
    _text = text.encode("utf-8")
    _url  = f'{api_url}&IG={b_session.headers.get("IG")}&IID=translator.{random.randint(5019, 5026)}.{random.randint(1, 3)}'
    if target_lang == 'zh-CN': target_lang = 'zh-Hans'
    _data = {'': '', 'fromLang': source_lang, 'to': target_lang, 'text': _text, 'token': b_session.headers.get('token'), 'key': b_session.headers.get('key')}
    try:
        _r = b_session.post(_url, data=_data)
        if _r.text=='':
            print(' Something is wrong with cn.bing.com request/response. Please try switching to google.')
        response = _r.json()
        # print(response)
        if type(response) is dict:
            if 'ShowCaptcha' in response.keys():
                b_session = _get_bing_session()
                return Bing_Translate(_text, target_lang, source_lang)
            elif 'statusCode' in response.keys():
                if response['statusCode'] == 400:
                    response['errorMessage'] = '1000 characters limit! You send {} characters.'.format(len(_text))
            else:
                return response['translations'][0]['text']
        else:
            return response[0]['translations'][0]['text']
    except Exception as e:
        print("Bing translate error: {}".format(e))
        return 'Bing translate error'


class TranslationManager:
    """Intelligent Translation Manager"""
    def __init__(self, max_workers=10):
        self.cache = {}
        self.lock = Lock()
        self.max_workers = max_workers
    
    def get_cache_key(self, text, lang):
        return f"{text}||{lang}"
    
    def translate_batch(self, texts, target_lang, source_lang='en'):
        """Batch translation of text"""
        results = {}
        to_translate = []
        
        # Check cache
        with self.lock:
            for text in texts:
                key = self.get_cache_key(text, target_lang)
                if key in self.cache:
                    results[text] = self.cache[key]
                else:
                    to_translate.append(text)
        
        if not to_translate:
            return results
        
        # Multithreaded translation of uncached text
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_text = {
                executor.submit(self._translate_single, text, target_lang, source_lang): text
                for text in to_translate
            }
            
            for future in as_completed(future_to_text):
                text = future_to_text[future]
                try:
                    translation = future.result()
                    results[text] = translation
                    
                    # Update cache
                    with self.lock:
                        key = self.get_cache_key(text, target_lang)
                        self.cache[key] = translation
                        
                except Exception as e:
                    print(f"  ⚠ translation error: {text[:30]}... -> {e}")
                    results[text] = text
        
        return results
    
    def _translate_single(self, text, target_lang, source_lang='en'):
        """Translate a single text"""
        if not text or not isinstance(text, str) or not text.strip():
            return text
        
        try:
            translation = Bing_Translate(text, target_lang, source_lang)             # Bing Translate
            # translation = Google_Translate(text, target_lang, source_lang)         # Google Translate
            time.sleep(0.05)  # Avoid API restrictions
            return translation
        except Exception as e:
            raise e


def collect_texts(data):
    """Collect all texts"""
    texts = set()
    
    def _collect(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                _collect(v)
        elif isinstance(obj, list):
            for item in obj:
                _collect(item)
        elif isinstance(obj, str) and obj.strip():
            texts.add(obj)
    
    _collect(data)
    return list(texts)

def apply_translations(data, translation_map):
    """Applied Translation"""
    if isinstance(data, dict):
        return {k: apply_translations(v, translation_map) for k, v in data.items()}
    elif isinstance(data, list):
        return [apply_translations(item, translation_map) for item in data]
    elif isinstance(data, str) and data in translation_map:
        return translation_map[data]
    return data

def translate_language(original_data, lang_code, texts, manager, output_dir):
    """Translate a single language"""
    filename = LANGUAGES[lang_code]
    print(f"\n[{lang_code}] Start translating {len(texts)} texts...")
    
    try:
        # batch translation
        translations = manager.translate_batch(texts, lang_code)
        # Applied Translation
        translated_data = apply_translations(original_data, translations)
        # Save file
        with open(output_dir / filename, 'w', encoding='utf-8', newline="\n") as f:     # newline="\n" -> Linux Style
            json.dump(translated_data, f, ensure_ascii=False, indent=2)
        
        print(f"[{lang_code}] ✓ complete: {filename}")
        return lang_code, True
        
    except Exception as e:
        print(f"[{lang_code}] ✗ failure: {e}")
        return lang_code, False

def main():
    global LANGUAGES
    print("🚀 Multi-thread translation starts\n")
    start_time = time.time()
    
    work_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

    # Create output directory
    output_dir = Path(work_dir + '/custom_components/elegoo_printer/translations')
    output_dir.mkdir(exist_ok=True)

    en_json_path = output_dir / 'en.json'
    # 1、 get en_json flie from github
    # if get_en_json_from_github(en_json_path):
        # with open(en_json_path, 'r', encoding='utf-8') as f:
            # original_data = json.load(f)
    
    # 2、 get en_json flie from Local
    try:
        with open(en_json_path, 'r', encoding='utf-8') as f:
            original_data = json.load(f)
    except FileNotFoundError:
        print("{en_json_path} File does not exist")
        return
    
    # collect text
    print("\n📝 Collect text to be translated...")
    texts = collect_texts(original_data)
    print(f"{len(texts)} unique texts found")
    
    # Create a translation manager
    manager = TranslationManager(max_workers=8)
    
    # Translate all languages in parallel
    print(f"\n🌍 Start translating {len(LANGUAGES)} languages...")
    print("-" * 60)
    
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for lang_code in LANGUAGES.keys():
            # if lang_code == 'en':
                # print(f'[{lang_code}] language skip')
                # continue
            future = executor.submit(translate_language, original_data, lang_code, texts, manager, output_dir)
            futures[future] = lang_code
        
        for future in as_completed(futures):
            lang_code, success = future.result()
            results[lang_code] = success
    
    # statistical results
    success_count = sum(1 for v in results.values() if v)
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 60)
    print(f"✨ Translation completed!")
    print(f"Success: {success_count}/{len(LANGUAGES)}")
    print(f"Processing time: {elapsed:.2f} seconds")
    print(f"Average per language: {elapsed/len(LANGUAGES):.2f} seconds")
    print(f"Output Directory: {output_dir.absolute()}")
    print("=" * 60)
    
    # Display result details
    print("\nDetailed results:")
    for lang_code in sorted(LANGUAGES.keys()):
        status = "✓" if results.get(lang_code) else "✗"
        print(f"  {status} {lang_code:8} -> {LANGUAGES[lang_code]}")


if __name__ == "__main__":
    main()
