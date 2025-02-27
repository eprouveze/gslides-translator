def implement_batch_recovery(text_dict, slide_metadata, source_language, target_language):
    """
    Implementation of a batch recovery system that:
    1. Saves progress after each batch
    2. Can resume from the last successful batch
    3. Can retry failed batches with smaller chunk sizes
    """
    import os
    import json
    import time
    from datetime import datetime
    
    # Create a directory for recovery files if it doesn't exist
    recovery_dir = "translation_recovery"
    os.makedirs(recovery_dir, exist_ok=True)
    
    # Create a unique recovery file name based on the presentation ID and timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    recovery_file = os.path.join(recovery_dir, f"recovery_{timestamp}.json")
    
    # Initialize or load recovery state
    if os.path.exists(recovery_file):
        with open(recovery_file, 'r', encoding='utf-8') as f:
            recovery_state = json.load(f)
        print(f"Resuming translation from recovery file: {recovery_file}")
    else:
        recovery_state = {
            "completed_batches": [],
            "failed_batches": [],
            "translated_items": {},
            "source_language": source_language,
            "target_language": target_language,
            "total_items": len(text_dict),
            "start_time": timestamp
        }
        # Save initial state
        with open(recovery_file, 'w', encoding='utf-8') as f:
            json.dump(recovery_state, f, ensure_ascii=False, indent=2)
        print(f"Created new recovery file: {recovery_file}")
    
    # Function to save state after each batch
    def save_recovery_state():
        with open(recovery_file, 'w', encoding='utf-8') as f:
            json.dump(recovery_state, f, ensure_ascii=False, indent=2)
    
    # Function to process a single batch
    def process_batch(batch, batch_index, batch_size):
        print(f"Processing batch {batch_index} with {len(batch)} items...")
        
        # Save batch to a temporary file for potential manual recovery
        batch_file = os.path.join(recovery_dir, f"batch_{batch_index}_{timestamp}.json")
        with open(batch_file, 'w', encoding='utf-8') as f:
            json.dump(batch, f, ensure_ascii=False, indent=2)
        
        # Attempt to translate the batch
        try:
            # [Your existing batch translation code goes here]
            # For example:
            # batch_result = translate_batch_with_claude(batch, slide_metadata, source_language, target_language)
            
            # Update the recovery state with successful results
            recovery_state["translated_items"].update(batch_result)
            recovery_state["completed_batches"].append(batch_index)
            save_recovery_state()
            
            print(f"Successfully processed batch {batch_index}")
            return True, batch_result
            
        except Exception as e:
            print(f"Error processing batch {batch_index}: {e}")
            
            # Log the failed batch for retry
            recovery_state["failed_batches"].append({
                "batch_index": batch_index,
                "items": list(batch.keys()),
                "error": str(e),
                "batch_size": batch_size
            })
            save_recovery_state()
            
            return False, {}
    
    # Function to retry failed batches with smaller sizes
    def retry_failed_batches():
        if not recovery_state["failed_batches"]:
            return
        
        print(f"\nRetrying {len(recovery_state['failed_batches'])} failed batches with smaller sizes...")
        
        for failed_batch_info in list(recovery_state["failed_batches"]):
            batch_index = failed_batch_info["batch_index"]
            items = failed_batch_info["items"]
            original_size = failed_batch_info["batch_size"]
            
            # Create smaller sub-batches (half the original size)
            new_batch_size = max(1, original_size // 2)
            
            print(f"Retrying batch {batch_index} with smaller size: {original_size} → {new_batch_size}")
            
            # Create a dictionary of items to retry
            retry_dict = {k: text_dict[k] for k in items if k in text_dict}
            
            # Split into smaller batches
            from math import ceil
            num_sub_batches = ceil(len(retry_dict) / new_batch_size)
            
            retry_items = list(retry_dict.items())
            sub_batches = [dict(retry_items[i:i+new_batch_size]) 
                           for i in range(0, len(retry_items), new_batch_size)]
            
            print(f"Split failed batch into {len(sub_batches)} smaller batches")
            
            # Process each sub-batch
            sub_success_count = 0
            for i, sub_batch in enumerate(sub_batches):
                print(f"Processing sub-batch {i+1}/{len(sub_batches)} for failed batch {batch_index}")
                
                # Wait briefly between retries to avoid rate limits
                time.sleep(2)
                
                success, result = process_batch(
                    sub_batch, 
                    f"{batch_index}.{i+1}", 
                    new_batch_size
                )
                
                if success:
                    sub_success_count += 1
                    # Update the recovery state with successful results
                    recovery_state["translated_items"].update(result)
                    save_recovery_state()
            
            # If all sub-batches succeeded, remove this batch from failed_batches
            if sub_success_count == len(sub_batches):
                recovery_state["failed_batches"].remove(failed_batch_info)
                print(f"Successfully recovered batch {batch_index}")
                save_recovery_state()
            else:
                print(f"Partial recovery of batch {batch_index}: {sub_success_count}/{len(sub_batches)} sub-batches")
    
    # Return the recovery system functions for use in the main script
    return {
        "process_batch": process_batch,
        "retry_failed_batches": retry_failed_batches,
        "recovery_file": recovery_file,
        "recovery_state": recovery_state,
        "save_recovery_state": save_recovery_state
    }

# Command-line utility for recovering failed translations
def recover_translation():
    """Command-line utility to resume a partially completed translation"""
    import os
    import json
    import argparse
    
    parser = argparse.ArgumentParser(description="Recover a failed translation")
    parser.add_argument("--recovery-file", required=True, help="Path to the recovery JSON file")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed batches with smaller chunks")
    parser.add_argument("--list", action="store_true", help="List all recovery files and their status")
    
    args = parser.parse_args()
    
    # List all recovery files
    if args.list:
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
                    print()
            except Exception as e:
                print(f"  {f} - Error reading file: {e}")
        
        return
    
    # Load the recovery file
    if not os.path.exists(args.recovery_file):
        print(f"Recovery file not found: {args.recovery_file}")
        return
    
    try:
        with open(args.recovery_file, 'r', encoding='utf-8') as f:
            recovery_state = json.load(f)
    except Exception as e:
        print(f"Error loading recovery file: {e}")
        return
    
    # Display recovery information
    total_items = recovery_state.get("total_items", 0)
    translated_items = len(recovery_state.get("translated_items", {}))
    failed_batches = recovery_state.get("failed_batches", [])
    
    print(f"Recovery file: {args.recovery_file}")
    print(f"Translation progress: {translated_items}/{total_items} items ({translated_items/total_items*100:.1f}%)")
    print(f"Failed batches: {len(failed_batches)}")
    
    # If there are failed batches and retry is requested
    if failed_batches and args.retry_failed:
        print("\nRetrying failed batches...")
        # Here you would implement the actual retry logic
        # This would require importing the main translation module
        # and calling the appropriate functions
        
        print("To retry failed batches, run the main script with:")
        print(f"  python slides_translator.py --resume {args.recovery_file}")
    
    if translated_items > 0:
        print("\nTo continue using successful translations, you can:")
        print("1. Run the main script with the --resume flag")
        print(f"  python slides_translator.py --resume {args.recovery_file}")
        print("2. Or extract just the translated items for manual use:")
        print(f"  python slides_translator.py --extract-translations {args.recovery_file}")

if __name__ == "__main__":
    # This allows the recovery utility to be run directly
    recover_translation()