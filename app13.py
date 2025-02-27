import os
import json
import anthropic
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import webbrowser
from tqdm import tqdm
import sys
import re
import time
import argparse
from datetime import datetime

# Configuration
SCOPES = ['https://www.googleapis.com/auth/presentations', 'https://www.googleapis.com/auth/drive']

def authenticate_google():
    creds = None
    token_path = 'token.json'
    regenerate_token = False

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            regenerate_token = True
        else:
            current_scopes = set(creds.scopes)
            required_scopes = set(SCOPES)
            if not required_scopes.issubset(current_scopes):
                print("Token has insufficient permissions. Regenerating...")
                regenerate_token = True
    else:
        regenerate_token = True

    if regenerate_token:
        if os.path.exists(token_path):
            os.remove(token_path)
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            
    slides_service = build('slides', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    return slides_service, drive_service

def update_slides(slides_service, drive_service, presentation_id, translated_texts, target_language):
    # First, get the original presentation 
    presentation = slides_service.presentations().get(presentationId=presentation_id).execute()
    original_title = presentation.get('title', 'Presentation')
    new_title = f"{original_title} - {target_language}"
    
    # Get file metadata to copy the presentation
    file_metadata = {
        'name': new_title,
        'mimeType': 'application/vnd.google-apps.presentation'
    }
    
    # Copy the file using Drive API
    copied_file = drive_service.files().copy(
        fileId=presentation_id, 
        body=file_metadata
    ).execute()
    
    new_presentation_id = copied_file['id']
    
    # Now update the new presentation with translated texts
    requests = []
    for object_id, new_text in translated_texts.items():
        # Check if it's a table cell (has the format objectId_r{row}_c{col})
        if '_r' in object_id and '_c' in object_id:
            # Parse the table cell coordinates
            base_id, row_col = object_id.split('_r', 1)
            row_idx, col_idx = row_col.split('_c')
            
            # For table cells, we need to use different update format
            requests.append({
                "deleteText": {
                    "objectId": base_id,
                    "cellLocation": {
                        "rowIndex": int(row_idx),
                        "columnIndex": int(col_idx)
                    },
                    "textRange": {"type": "ALL"}
                }
            })
            requests.append({
                "insertText": {
                    "objectId": base_id,
                    "cellLocation": {
                        "rowIndex": int(row_idx),
                        "columnIndex": int(col_idx)
                    },
                    "insertionIndex": 0,
                    "text": new_text
                }
            })
        else:
            # Regular text elements
            requests.append({"deleteText": {"objectId": object_id, "textRange": {"type": "ALL"}}})
            requests.append({"insertText": {"objectId": object_id, "insertionIndex": 0, "text": new_text}})
    
    # Process requests in batches since there might be a limit on request size
    batch_size = 100  # Adjust batch size as needed
    for i in range(0, len(requests), batch_size):
        batch_requests = requests[i:i + batch_size]
        try:
            slides_service.presentations().batchUpdate(
                presentationId=new_presentation_id, 
                body={"requests": batch_requests}
            ).execute()
        except Exception as e:
            print(f"Error in update batch {i//batch_size + 1}: {e}")
            # Print the first few problematic requests for debugging
            problem_batch = requests[i:i+min(5, batch_size)]
            print(f"Problem might be in these requests: {json.dumps(problem_batch, indent=2)}")
    
    return new_presentation_id

def list_recovery_files():
    """List all available recovery files and their status"""
    recovery_dir = "translation_recovery"
    if not os.path.exists(recovery_dir):
        print("No recovery directory found.")
        return
    
    recovery_files = [f for f in os.listdir(recovery_dir) if f.startswith("recovery_") and f.endswith(".json")]
    
    if not recovery_files:
        print("No recovery files found.")
        return
    
    print(f"Found {len(recovery_files)} recovery files:")
    for f in recovery_files:
        file_path = os.path.join(recovery_dir, f)
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
                total = data.get("total_items", 0)
                translated = len(data.get("translated_items", {}))
                failed = len(data.get("failed_batches", []))
                progress = (translated / total * 100) if total > 0 else 0
                
                print(f"  {f}")
                print(f"    Progress: {progress:.1f}% ({translated}/{total} items)")
                print(f"    Failed batches: {failed}")
                print(f"    Start time: {data.get('start_time', 'unknown')}")
                print(f"    Last updated: {data.get('last_updated', 'unknown')}")
                print()
        except Exception as e:
            print(f"  {f} - Error reading file: {e}")

def extract_text(service, presentation_id):  
    presentation = service.presentations().get(presentationId=presentation_id).execute()  
    slides = presentation.get('slides', [])  
    text_dict = {}  
    slide_metadata = []  # Store structured context  
      
    for index, slide in enumerate(slides):  
        slide_info = {  
            "slide_number": index + 1,  
            "title": "",  
            "content": []  
        }  
          
        # Process regular shape elements (text boxes, etc.)
        for element in slide.get('pageElements', []):  
            # Handle shapes (text boxes, etc.)
            shape = element.get('shape')  
            if shape and 'text' in shape:  
                text_elements = shape.get('text', {}).get('textElements', [])  
                full_text = ""
                
                try:
                    full_text = "".join(te.get('textRun', {}).get('content', '') for te in text_elements if 'textRun' in te).strip()
                except Exception as e:
                    print(f"Error processing text element: {e}")
                    continue
                    
                if full_text:  
                    text_dict[element['objectId']] = full_text  
                    slide_info["content"].append(full_text)
            
            # Handle tables
            table = element.get('table')
            if table:
                for row_idx, row in enumerate(table.get('tableRows', [])):
                    for col_idx, cell in enumerate(row.get('tableCells', [])):
                        if 'text' in cell:
                            text_elements = cell.get('text', {}).get('textElements', [])
                            cell_text = ""
                            
                            try:
                                cell_text = "".join(te.get('textRun', {}).get('content', '') for te in text_elements if 'textRun' in te).strip()
                            except Exception as e:
                                print(f"Error processing table cell: {e}")
                                continue
                                
                            if cell_text:
                                # Create a unique ID for the table cell
                                cell_id = f"{element['objectId']}_r{row_idx}_c{col_idx}"
                                text_dict[cell_id] = cell_text
                                slide_info["content"].append(cell_text)
          
        slide_metadata.append(slide_info)  
      
    return text_dict, slide_metadata

def split_dict_into_smart_batches(input_dict, max_input_tokens=150000, prompt_tokens=2000):
    """
    Split a dictionary into batches based on estimated token count to optimize API usage.
    """
    # Function to estimate tokens in a string (roughly 4 characters per token)
    def estimate_tokens(text):
        if text is None:
            return 0
        return len(str(text)) // 4 + 1  # Add 1 to round up
    
    items = list(input_dict.items())
    batches = []
    current_batch = {}
    current_token_count = prompt_tokens
    
    # Sort items by estimated token length (optional)
    items.sort(key=lambda x: estimate_tokens(x[1]), reverse=True)
    
    for key, value in items:
        item_tokens = estimate_tokens(key) + estimate_tokens(value) + 10  # +10 for JSON formatting
        
        if current_token_count + item_tokens > max_input_tokens and current_batch:
            batches.append(current_batch)
            current_batch = {}
            current_token_count = prompt_tokens
        
        current_batch[key] = value
        current_token_count += item_tokens
    
    if current_batch:
        batches.append(current_batch)
    
    total_items = len(input_dict)
    batch_sizes = [len(batch) for batch in batches]
    avg_batch_size = sum(batch_sizes) / len(batches) if batches else 0
    
    print(f"Created {len(batches)} batches from {total_items} items")
    print(f"Batch sizes: min={min(batch_sizes) if batches else 0}, max={max(batch_sizes) if batches else 0}, avg={avg_batch_size:.1f}")
    print(f"Estimated token usage efficiency: {(sum(batch_sizes)/total_items)*100:.1f}%")
    
    return batches

def repair_json(json_content):
    """
    More robust JSON repair function that can handle various common issues.
    """
    original_content = json_content
    try:
        return json.loads(json_content)
    except json.JSONDecodeError as e:
        print(f"Initial JSON parsing error: {e}")
        
        if "Unterminated string" in str(e):
            error_info = str(e)
            line_match = re.search(r'line (\d+)', error_info)
            col_match = re.search(r'column (\d+)', error_info)
            
            if line_match and col_match:
                line_num = int(line_match.group(1))
                col_num = int(col_match.group(1))
                lines = json_content.split('\n')
                if 0 <= line_num-1 < len(lines):
                    line = lines[line_num-1]
                    if col_num-1 < len(line) and line[col_num-1] == '"':
                        line = line[:col_num-1] + '\\"' + line[col_num:]
                    else:
                        line = line + '"'
                    lines[line_num-1] = line
                    json_content = '\n'.join(lines)
        
        brace_count = json_content.count('{') - json_content.count('}')
        if brace_count > 0:
            json_content = json_content + ('}' * brace_count)
        elif brace_count < 0:
            for _ in range(-brace_count):
                json_content = json_content.rstrip().rstrip('}').rstrip()
        
        json_content = re.sub(r',\s*}', '}', json_content)
        json_content = re.sub(r',\s*]', ']', json_content)
        
        def fix_property_names(match):
            prop = match.group(1)
            if not (prop.startswith('"') and prop.endswith('"')):
                return f'"{prop}":'
            return match.group(0)
        
        json_content = re.sub(r'([a-zA-Z0-9_]+):', fix_property_names, json_content)
        
        try:
            return json.loads(json_content)
        except json.JSONDecodeError as e2:
            print(f"JSON repair attempt failed: {e2}")
            result = {}
            pattern = r'"([^"]+)"\s*:\s*"([^"]*)"|"([^"]+)"\s*:\s*([0-9]+)'
            for match in re.finditer(pattern, original_content):
                groups = match.groups()
                if groups[0] is not None:
                    result[groups[0]] = groups[1]
                else:
                    result[groups[2]] = int(groups[3])
            
            if result:
                print(f"Managed to extract {len(result)} key-value pairs through regex")
                return result
            
            raise e

def extract_json_blocks(text):
    """
    Extract valid JSON blocks from text that might contain multiple partial JSON objects.
    """
    potential_blocks = re.findall(r'({[^{]*?})', text)
    valid_blocks = []
    for block in potential_blocks:
        try:
            parsed = json.loads(block)
            valid_blocks.append(parsed)
        except:
            pass
    
    if valid_blocks:
        combined = {}
        for block in valid_blocks:
            combined.update(block)
        return combined
        
    return None

def setup_recovery_system(presentation_id, text_dict, slide_metadata, source_language, target_language, resume_file=None):
    """
    Set up a recovery system for batch processing.
    """
    recovery_dir = "translation_recovery"
    os.makedirs(recovery_dir, exist_ok=True)
    
    if resume_file and os.path.exists(resume_file):
        with open(resume_file, 'r', encoding='utf-8') as f:
            recovery_state = json.load(f)
        print(f"Resuming translation from recovery file: {resume_file}")
        recovery_file = resume_file
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        recovery_file = os.path.join(recovery_dir, f"recovery_{presentation_id}_{timestamp}.json")
        recovery_state = {
            "presentation_id": presentation_id,
            "completed_batches": [],
            "failed_batches": [],
            "translated_items": {},
            "source_language": source_language,
            "target_language": target_language,
            "total_items": len(text_dict),
            "start_time": timestamp,
            "last_updated": timestamp
        }
        with open(recovery_file, 'w', encoding='utf-8') as f:
            json.dump(recovery_state, f, ensure_ascii=False, indent=2)
        print(f"Created new recovery file: {recovery_file}")
    
    def save_recovery_state():
        recovery_state["last_updated"] = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(recovery_file, 'w', encoding='utf-8') as f:
            json.dump(recovery_state, f, ensure_ascii=False, indent=2)
    
    return recovery_state, recovery_file, save_recovery_state

def translate_batch(batch, batch_index, slide_metadata, source_language, target_language, max_retries=2):
    """
    Translate a single batch with retry logic.
    """
    batch_copy = batch.copy()
    
    def clean_text(text):
        return text.replace('\\n', '\n').replace('\\u000b', '\v').replace('\\t', '\t')
    
    client = anthropic.Anthropic(
        api_key=os.getenv("CLAUDE_API_KEY"),
        default_headers={
            "anthropic-beta": "output-128k-2025-02-19"
        }
    )
    
    structured_context = json.dumps(slide_metadata, ensure_ascii=False, indent=2)
    
    system_prompt = f"""You are a professional translator. Translate from {source_language} to {target_language}.
Ensure consistency in terminology and contextual meaning.

IMPORTANT: If you encounter text that appears to already be in {target_language}, preserve it exactly as is without any changes.
Do not translate text that is already in {target_language}.

PRIVACY NOTICE: Do not store, learn from, or retain any of the content provided for translation.
This is confidential material that should only be processed for immediate translation purposes."""
    
    user_message = f"""
Translate the following JSON object from {source_language} to {target_language}.
Consider the structured slide context provided below for context and consistency.

IMPORTANT INSTRUCTIONS:
- If any text appears to already be in {target_language}, keep it exactly as is.
- Only translate text that is in {source_language}.
- Do NOT include escape sequences for newlines (\\n) or other characters - use the actual characters.
- Return VALID JSON format with all keys and values properly enclosed in double quotes.
- Ensure all property names and string values are properly quoted with double quotes.
- Do not include any trailing commas.

This is batch {batch_index} with {len(batch_copy)} items.

Slide Context:
{structured_context}

Now translate the following structured JSON object while preserving its format:
{json.dumps(batch_copy, ensure_ascii=False, indent=2)}

Reply ONLY with the translated JSON. The JSON MUST be valid and parseable.
"""

    for retry in range(max_retries + 1):
        try:
            response = client.messages.create(
                model="claude-3-7-sonnet-20250219",
                system=system_prompt,
                max_tokens=4000,
                messages=[
                    {"role": "user", "content": user_message}
                ],
                metadata={
                    "user_id": "anonymous_user",
                    "privacy": "strict"
                }
            )

            translated_text = response.content[0].text
            
            if "```json" in translated_text:
                json_content = translated_text.split("```json")[1].split("```")[0].strip()
            elif "```" in translated_text:
                json_content = translated_text.split("```")[1].strip()
            else:
                json_content = translated_text.strip()
            
            try:
                batch_result = json.loads(json_content)
            except json.JSONDecodeError as e:
                try:
                    batch_result = repair_json(json_content)
                except Exception as e2:
                    extracted_result = extract_json_blocks(json_content)
                    if extracted_result:
                        batch_result = extracted_result
                        print(f"Extracted {len(batch_result)} items through JSON block extraction")
                    else:
                        if retry < max_retries:
                            print(f"JSON parsing failed on attempt {retry+1}, retrying...")
                            time.sleep(3)
                            continue
                        else:
                            raise e
            
            for key, value in batch_result.items():
                if isinstance(value, str):
                    batch_result[key] = clean_text(value)
            
            print(f"Successfully processed batch {batch_index}")
            return batch_result
                
        except Exception as e:
            if retry < max_retries:
                print(f"Error in batch {batch_index} (attempt {retry+1}): {e}")
                print(f"Retrying in 5 seconds...")
                time.sleep(5)
            else:
                print(f"All {max_retries + 1} attempts failed for batch {batch_index}: {e}")
                raise e

def translate_text(text_dict, slide_metadata, source_language, target_language, resume_file=None):
    client = anthropic.Anthropic(
        api_key=os.getenv("CLAUDE_API_KEY"),
        default_headers={
            "anthropic-beta": "output-128k-2025-02-19"
        }
    )
    
    def deduplicate_content(input_dict):
        content_to_keys = {}
        for key, value in input_dict.items():
            if value in content_to_keys:
                content_to_keys[value].append(key)
            else:
                content_to_keys[value] = [key]
        
        unique_content = {}
        duplicates_map = {}
        
        for content, keys in content_to_keys.items():
            representative_key = keys[0]
            unique_content[representative_key] = content
            for key in keys:
                duplicates_map[key] = representative_key
        
        print(f"Found {len(input_dict) - len(unique_content)} duplicate content items")
        print(f"Reduced from {len(input_dict)} to {len(unique_content)} unique content items to translate")
        
        return unique_content, duplicates_map
    
    recovery_state, recovery_file, save_recovery_state = setup_recovery_system(
        "custom_id", text_dict, slide_metadata, source_language, target_language, resume_file
    )
    
    if not recovery_state["translated_items"]:
        unique_text_dict, duplicates_map = deduplicate_content(text_dict)
        recovery_state["duplicates_map"] = duplicates_map
        save_recovery_state()
    else:
        unique_text_dict = {k: text_dict[k] for k in text_dict.keys() 
                          if k not in [rep_key for rep_key in recovery_state["duplicates_map"].values()]}
        duplicates_map = recovery_state["duplicates_map"]
        print(f"Resumed with {len(recovery_state['translated_items'])} already translated items")
    
    remaining_dict = {k: v for k, v in unique_text_dict.items() 
                     if k not in recovery_state["translated_items"]}
    
    if not remaining_dict:
        print("All items have already been translated. Nothing to do.")
        full_translated_dict = recovery_state["translated_items"].copy()
    else:
        batches = split_dict_into_smart_batches(remaining_dict, max_input_tokens=150000, prompt_tokens=2000)
        print(f"Splitting translation into {len(batches)} batches")
        
        unique_translated_dict = recovery_state["translated_items"].copy()
        
        with tqdm(total=len(batches), desc="Translating", unit="batch") as pbar:
            for batch_index, batch in enumerate(batches):
                batch_id = f"batch_{batch_index+1}"
                if batch_id in recovery_state["completed_batches"]:
                    print(f"Skipping already completed batch {batch_id}")
                    pbar.update(1)
                    continue
                
                print(f"\nProcessing batch {batch_index+1} of {len(batches)} with {len(batch)} items...")
                
                try:
                    batch_result = translate_batch(
                        batch, batch_index+1, slide_metadata, 
                        source_language, target_language
                    )
                    
                    unique_translated_dict.update(batch_result)
                    recovery_state["translated_items"].update(batch_result)
                    recovery_state["completed_batches"].append(batch_id)
                    save_recovery_state()
                    
                except Exception as e:
                    print(f"Error in batch {batch_index+1}: {e}")
                    recovery_state["failed_batches"].append({
                        "batch_id": batch_id,
                        "keys": list(batch.keys()),
                        "error": str(e)
                    })
                    save_recovery_state()
                    print("Continuing with next batch...")
                
                pbar.update(1)
                completion_percentage = int(100 * (batch_index + 1) / len(batches))
                pbar.set_description(f"Translating: {completion_percentage}% complete")
        
        print(f"\nTranslation of unique content completed with {len(unique_translated_dict)} items out of {len(unique_text_dict)} unique items")
        
        if recovery_state["failed_batches"]:
            print(f"\nRetrying {len(recovery_state['failed_batches'])} failed batches with smaller chunks...")
            
            for failed_batch in list(recovery_state["failed_batches"]):
                batch_id = failed_batch["batch_id"]
                keys = failed_batch["keys"]
                
                retry_batch = {k: text_dict[k] for k in keys if k in text_dict}
                chunk_size = max(5, len(retry_batch) // 4)
                retry_items = list(retry_batch.items())
                sub_batches = [dict(retry_items[i:i+chunk_size]) 
                               for i in range(0, len(retry_items), chunk_size)]
                
                print(f"Split failed batch {batch_id} into {len(sub_batches)} smaller chunks of size ~{chunk_size}")
                
                for i, sub_batch in enumerate(sub_batches):
                    sub_id = f"{batch_id}_sub_{i+1}"
                    
                    try:
                        print(f"Processing sub-batch {i+1}/{len(sub_batches)} for failed batch {batch_id}")
                        sub_result = translate_batch(
                            sub_batch, f"{batch_id}.{i+1}", slide_metadata, 
                            source_language, target_language, max_retries=3
                        )
                        unique_translated_dict.update(sub_result)
                        recovery_state["translated_items"].update(sub_result)
                        recovery_state["completed_batches"].append(sub_id)
                        save_recovery_state()
                        
                    except Exception as e:
                        print(f"Error in sub-batch {i+1} of failed batch {batch_id}: {e}")
                        continue
                
                recovery_state["failed_batches"].remove(failed_batch)
                save_recovery_state()
        
        full_translated_dict = {}
        for key, value in unique_translated_dict.items():
            full_translated_dict[key] = value
        
        for original_key, rep_key in duplicates_map.items():
            if rep_key in unique_translated_dict and original_key != rep_key:
                full_translated_dict[original_key] = unique_translated_dict[rep_key]
    
    print(f"Reconstructed full translation dictionary with {len(full_translated_dict)} items")
    
    missing_keys = set(text_dict.keys()) - set(full_translated_dict.keys())
    if missing_keys:
        print(f"Warning: {len(missing_keys)} keys were not translated: {list(missing_keys)[:5]}...")
        if len(missing_keys) > 0:
            print(f"Attempting to translate {len(missing_keys)} missing keys in a final batch...")
            missing_dict = {k: text_dict[k] for k in missing_keys if k in text_dict}
            
            try:
                structured_context = json.dumps(slide_metadata, ensure_ascii=False, indent=2)
                
                system_prompt = f"""You are a professional translator. Translate from {source_language} to {target_language}.
Ensure consistency in terminology and contextual meaning.

IMPORTANT: If you encounter text that appears to already be in {target_language}, preserve it exactly as is.
Do not translate text that is already in {target_language}."""
                
                user_message = f"""
Translate the following JSON object from {source_language} to {target_language}.
This is a final batch to catch any missing translations.

IMPORTANT INSTRUCTIONS:
- If any text appears to already be in {target_language}, keep it exactly as is.
- Only translate text that is in {source_language}.
- Do NOT include escape sequences for newlines (\\n) or other characters - use the actual characters.
- Return VALID JSON format with all keys and values properly enclosed in double quotes.

Now translate the following structured JSON object:
{json.dumps(missing_dict, ensure_ascii=False, indent=2)}

Reply ONLY with the translated JSON.
"""
                
                response = client.messages.create(
                    model="claude-3-7-sonnet-20250219",
                    system=system_prompt,
                    max_tokens=4000,
                    messages=[
                        {"role": "user", "content": user_message}
                    ],
                    metadata={
                        "user_id": "anonymous_user"
                    }
                )
                
                translated_text = response.content[0].text
                
                if "```json" in translated_text:
                    json_content = translated_text.split("```json")[1].split("```")[0].strip()
                elif "```" in translated_text:
                    json_content = translated_text.split("```")[1].strip()
                else:
                    json_content = translated_text.strip()
                
                try:
                    final_batch = json.loads(json_content)
                except json.JSONDecodeError:
                    try:
                        final_batch = repair_json(json_content)
                    except:
                        extracted = extract_json_blocks(json_content)
                        if extracted:
                            final_batch = extracted
                        else:
                            raise
                
                def clean_text(text):
                    return text.replace('\\n', '\n').replace('\\u000b', '\v').replace('\\t', '\t')
                
                for key, value in final_batch.items():
                    if isinstance(value, str):
                        final_batch[key] = clean_text(value)
                
                full_translated_dict.update(final_batch)
                recovery_state["translated_items"].update(final_batch)
                save_recovery_state()
                
                print(f"Successfully processed final batch with {len(final_batch)} additional items")
                
                missing_keys = set(text_dict.keys()) - set(full_translated_dict.keys())
                if missing_keys:
                    print(f"Final warning: {len(missing_keys)} keys still not translated: {list(missing_keys)[:5]}...")
                else:
                    print("All items successfully translated!")
                    
            except Exception as e:
                print(f"Failed to process final batch: {e}")
    else:
        print("All items successfully translated!")
    
    return full_translated_dict

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Slides Translator")
    parser.add_argument("--resume", help="Resume translation from a recovery file")
    parser.add_argument("--list-recovery", action="store_true", help="List available recovery files")
    parser.add_argument("--presentation-id", help="Google Slides Presentation ID")
    parser.add_argument("--source-language", help="Source language code (e.g., en)")
    parser.add_argument("--target-language", help="Target language code (e.g., ja)")
    
    args = parser.parse_args()
    
    if args.list_recovery:
        list_recovery_files()
        sys.exit(0)
    
    if args.presentation_id:
        presentation_id = args.presentation_id
    else:
        presentation_id = input("Enter Google Slides Presentation ID: ")
    
    if args.source_language:
        source_language = args.source_language
    else:
        source_language = input("Enter source language (e.g., en for English): ")
    
    if args.target_language:
        target_language = args.target_language
    else:
        target_language = input("Enter target language (e.g., fr for French): ")
    
    slides_service, drive_service = authenticate_google()
    
    extracted_text, slide_metadata = extract_text(slides_service, presentation_id)
    
    translated_texts = translate_text(extracted_text, slide_metadata, source_language, target_language, args.resume)
    
    new_presentation_id = update_slides(slides_service, drive_service, presentation_id, translated_texts, target_language)
    
    presentation_url = f"https://docs.google.com/presentation/d/{new_presentation_id}/edit"
    
    print(f"Translation completed!")
    print(f"New presentation created with ID: {new_presentation_id}")
    print(f"Opening the translated presentation in your browser...")
    
    webbrowser.open(presentation_url)
