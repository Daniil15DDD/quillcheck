# QuillCheck Word add-in

A side panel for Word (web and Mac desktop) that uses this app's local models. It marks the
quarter of sentences that score most AI-like *within your document*, and for each one you can
get a local rewrite suggestion and choose Replace or Skip. Everything runs on this Mac.

## One-time setup

1. Create and trust a local HTTPS certificate. macOS asks for your password:

   ```
   npx office-addin-dev-certs install
   ```

2. Start the server and open https://localhost:3000/taskpane.html in Chrome. It should load
   with no certificate warning. If Chrome asks to allow local network access, allow it.

   ```
   ./addin/run_addin.sh        # run from the QuillCheck folder
   ```

3. Add the panel to Word:
   - **Word in the browser:** open a document, then go to Home → Add-ins → More Add-ins →
     My Add-ins → **Upload My Add-in** and choose `addin/manifest.xml` from the QuillCheck folder.
     You may need to upload it again in a new browser session.
   - **Word for Mac:** copy `manifest.xml` into
     `~/Library/Containers/com.microsoft.Word/Data/Documents/wef/` (create the folder if needed),
     restart Word (Cmd+Q), then find it under Home → Add-ins → Developer Add-ins.
     Double-clicking `addin/install_word_mac.command` does the copy. If macOS says
     "Operation not permitted", use Finder instead: press Cmd+Shift+G, go to the folder above
     and drag `manifest.xml` in.

A **Check text** button appears on the Home tab.

## Each time

Run `addin/run_addin.sh` and keep it running, then click **Check text** in Word.

- **Scan document**: about 15–30 seconds. Marked sentences turn yellow.
- **Selected text only**: scans just the paragraphs you selected (quicker on long documents).
- **Auto-scan**: rescans about 5 seconds after you stop typing.
- **Suggest rewrite / Suggest all**: shows a word-level diff and any check warnings.
  Replace swaps in the suggestion, Skip keeps your sentence. Either one removes the mark.
- **Clear marks**: removes all yellow marks and leaves your text as it is.

## Things to know

- Marks are relative. Every text has a "most AI-like quarter", even one you wrote yourself.
  Scores for single sentences are noisy. In testing, plainly human sentences were marked.
- Removing a mark also removes any yellow highlight you had added yourself on that sentence.
- Sentences with soft line breaks or unusual formatting sometimes can't be located. The panel
  says how many.
- Rewrites come from the same small local model as the Rewrite tab. Read each one before
  you accept it. They are not designed to change any detector's result.
