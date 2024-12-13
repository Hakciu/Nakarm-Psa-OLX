import requests
from bs4 import BeautifulSoup
import re
import time
import threading
import concurrent.futures
import multiprocessing
import customtkinter as ctk
from tkinter import messagebox
import uuid

# Ustawienia CustomTkinter
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

BASE_URL = "https://nakarmpsa.olx.pl/"
VOTE_URL = "https://nakarmpsa.olx.pl/wp-content/themes/olx-nakarm-psa/vote.php"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive"
}
HEADERS_VOTE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": BASE_URL,
    "Connection": "keep-alive",
}

def pobierz_nonce(session):
    """
    Pobiera nonce ze strony głównej.
    """
    response = session.get(BASE_URL, headers=HEADERS)
    response.raise_for_status()
    html_content = response.text
    nonce_pattern = re.compile(r'"nonce":"([a-f0-9]+)"')
    match = nonce_pattern.search(html_content)
    if match:
        return match.group(1)
    raise ValueError("Nie znaleziono nonce na stronie.")

def pobierz_zwierzeta(session):
    """
    Pobiera listę zwierząt dostępnych do nakarmienia.
    Filtruje zwierzęta, które mają już 100% głosów.
    """
    response = session.get(BASE_URL, headers=HEADERS)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    pet_elements = soup.select("div.olx-pet-list-inner .single-pet")
    pets = []

    for pet in pet_elements:
        pet_id = pet.get("data-pet-id")
        pet_name = pet.get("data-pet-name")
        pet_votes = pet.get("data-pet-votes")
        pet_type = pet.get("data-pet-type")

        if pet_id and pet_name and pet_votes != "100":
            pets.append({
                "id": pet_id,
                "name": pet_name,
                "votes": pet_votes,
                "type": pet_type
            })
    return pets

def nakarm_psa(session, nonce, pet_id, max_retries=5):
    """
    Wysyła żądanie nakarmienia psa.
    Implementuje mechanizm retry z wykładniczym backoffiem w przypadku błędów.
    """
    payload = {
        "nonce": nonce,
        "user_id": str(uuid.uuid4()),
        "pet_id": str(pet_id),
        "action": "feedPet",
        "crossDomain": "true",
        "xhrFields": "[object Object]"
    }
    backoff_factor = 1  # Sekundy

    for attempt in range(max_retries):
        try:
            response = session.post(VOTE_URL, headers=HEADERS_VOTE, data=payload)
            response.raise_for_status()
            json_response = response.json()

            if json_response.get("success"):
                return True
            else:
                errors = json_response.get("data", {}).get("messages", {}).get("errors", [])
                if 'already-voted' in errors:
                    print(f"Zwierzak o ID {pet_id} już został nakarmiony. Pomijam.")
                    return 'already-voted'
                else:
                    print(f"Niepowodzenie karmienia zwierzaka o ID: {pet_id}")
                    print(f"Powód (json_response): {json_response}")
        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                wait_time = backoff_factor * (2 ** attempt)
                print(f"Błąd 429. Próba ponowienia za {wait_time} sekund.")
                time.sleep(wait_time)
                continue
            else:
                print(f"Niepowodzenie z powodu wyjątku requests: {e} dla pet_id: {pet_id}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"Niepowodzenie z powodu wyjątku requests: {e} dla pet_id: {pet_id}")
            return False
    print(f"Nie udało się nakarmić zwierzaka o ID {pet_id} po {max_retries} próbach.")
    return False

class FeedPetsApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Nakarm psa OLX")
        self.geometry("700x650")
        self.resizable(False, False)
        self.is_running = False
        self.steps_done = 0
        self.total_steps = 0
        self.start_time = None
        self.current_pet_name = "--"
        self.use_multithreading = ctk.BooleanVar(value=False)
        self.lock = threading.Lock()

        main_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#2c2f33")
        main_frame.pack(pady=20, padx=20, fill="both", expand=True)

        # Pole do wprowadzania liczby powtórzeń
        iterations_label = ctk.CTkLabel(main_frame, text="Liczba powtórzeń:", font=("Roboto", 18))
        iterations_label.pack(anchor="center", pady=(20, 10))

        self.iterations_var = ctk.StringVar(value="1")
        self.iterations_entry = ctk.CTkEntry(main_frame, textvariable=self.iterations_var, width=100, font=("Roboto", 18))
        self.iterations_entry.pack(anchor="center")

        # Checkbox do wyboru użycia wielowątkowości
        self.multithreading_checkbox = ctk.CTkCheckBox(
            main_frame, text="Użyj wielowątkowości", variable=self.use_multithreading, font=("Roboto", 16)
        )
        self.multithreading_checkbox.pack(anchor="center", pady=(10, 20))

        # Przycisk do rozpoczęcia procesu karmienia
        self.start_button = ctk.CTkButton(main_frame, text="Zacznij karmić", command=self.start_feeding, font=("Roboto", 18), width=200, height=50)
        self.start_button.pack(pady=20)

        # Aktualny postęp karmienia w ramach pojedynczej iteracji
        self.current_progress_label = ctk.CTkLabel(main_frame, text="Aktualny postęp karmienia: 0%", font=("Roboto", 18))
        self.current_progress_label.pack(anchor="center", pady=(20, 10))

        self.current_progress = ctk.CTkProgressBar(main_frame, width=500, height=25)
        self.current_progress.set(0)
        self.current_progress.pack(anchor="center", pady=(0, 40))

        # Ogólny postęp karmienia
        self.overall_progress_label = ctk.CTkLabel(main_frame, text="Ogólny postęp: 0%", font=("Roboto", 18))
        self.overall_progress_label.pack(anchor="center", pady=(20, 10))

        self.overall_progress = ctk.CTkProgressBar(main_frame, width=500, height=25)
        self.overall_progress.set(0)
        self.overall_progress.pack(anchor="center", pady=(0, 10))

        # Czas do końca procesu
        self.remaining_time_label = ctk.CTkLabel(main_frame, text="Czas do końca: --:--", font=("Roboto", 16))
        self.remaining_time_label.pack(anchor="center", pady=(10, 40))

        # Informacja o aktualnie karmionym zwierzęciu
        self.current_pet_label = ctk.CTkLabel(main_frame, text="Nakarmiono: --", font=("Roboto", 20))
        self.current_pet_label.pack(anchor="center", pady=(40, 10))

    def start_feeding(self):
        """
        Rozpoczyna proces karmienia zwierząt.
        """
        if self.is_running:
            messagebox.showwarning("Uwaga", "Proces karmienia już trwa.")
            return

        try:
            iterations = int(self.iterations_var.get())
            if iterations < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Błąd", "Proszę wprowadzić poprawną liczbę powtórzeń.")
            return

        self.is_running = True
        self.start_button.configure(state="disabled")
        self.current_progress.set(0)
        self.overall_progress.set(0)
        self.steps_done = 0
        self.start_time = time.time()

        # Uruchamia w osobnym wątku, aby nie blokować interfejsu
        threading.Thread(target=self.feed_pets, args=(iterations,), daemon=True).start()

    def feed_pets(self, iterations):
        """
        Pobiera zwierzęta i rozpoczyna proces karmienia.
        W zależności od ustawienia, używa wielowątkowości.
        """
        session = requests.Session()
        try:
            pets = pobierz_zwierzeta(session)
            if not pets:
                self.show_message("Brak zwierząt do nakarmienia.")
                self.reset_ui()
                return

            self.total_steps = len(pets) * iterations

            if self.use_multithreading.get():
                num_threads = min(4, multiprocessing.cpu_count() - 1)
                chunks = [pets[i::num_threads] for i in range(num_threads)]

                with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                    futures = [executor.submit(self.process_chunk, session, chunk, iterations) for chunk in chunks]
                    concurrent.futures.wait(futures)
            else:
                self.process_chunk(session, pets, iterations)

            self.after(0, lambda: self.show_message(f"Dziękuję, że nakarmiłeś/aś {len(pets)} zwierząt {iterations} razy!"))
        except Exception as e:
            self.after(0, lambda: self.show_message(f"Wystąpił błąd: {e}"))
        finally:
            self.after(0, self.reset_ui)

    def process_chunk(self, session, pets, iterations):
        """
        Przetwarza podział zwierząt, wykonując karmienie dla każdej iteracji.
        """
        for _ in range(iterations):
            session = requests.Session()
            pets_copy = pets.copy()
            for pet in pets_copy:
                if not self.is_running:
                    return
                try:
                    session.cookies.clear()
                    nonce = pobierz_nonce(session)
                    result = nakarm_psa(session, nonce, pet["id"])
                    if result == True:
                        success = True
                    elif result == 'already-voted':
                        success = False
                        with self.lock:
                            pets.remove(pet)
                    else:
                        success = False
                except Exception as e:
                    print(f"Wyjątek podczas karmienia pet_id {pet['id']}: {e}")
                    success = False

                with self.lock:
                    self.steps_done += 1
                    if success:
                        self.current_pet_name = pet["name"]
                    else:
                        if result == 'already-voted':
                            self.current_pet_name = f"{pet['name']} (już pokarmiony)"
                        else:
                            self.current_pet_name = f"{pet['name']} (niepowodzenie)"
                    # Aktualizacja UI
                    self.after(0, self.update_progress)

                # Opóźnienie między żądaniami
                time.sleep(0.05)  # 50 ms

    def update_progress(self):
        """
        Aktualizuje wskaźniki postępu w interfejsie użytkownika.
        """
        steps_done = self.steps_done
        total_steps = self.total_steps

        # Ogólny postęp
        progress_fraction = (steps_done / total_steps) if total_steps > 0 else 0
        self.overall_progress.set(progress_fraction)
        self.overall_progress_label.configure(text=f"Ogólny postęp: {int(progress_fraction * 100)}%")

        # Aktualny postęp (w ramach pojedynczej iteracji)
        iterations = int(self.iterations_var.get())
        pets_count = (self.total_steps // iterations) if iterations > 0 else 1

        current_iteration_fraction = ((steps_done % pets_count) / pets_count) if pets_count > 0 else 0
        self.current_progress.set(current_iteration_fraction)
        self.current_progress_label.configure(text=f"Aktualny postęp karmienia: {int(current_iteration_fraction * 100)}%")

        # Czas do końca
        if steps_done > 0:
            elapsed_time = time.time() - self.start_time
            remaining_time = (elapsed_time / steps_done) * (total_steps - steps_done)
        else:
            remaining_time = 0

        self.remaining_time_label.configure(text=f"Czas do końca: {self.format_time(remaining_time)}")
        self.current_pet_label.configure(text=f"Nakarmiono: {self.current_pet_name}")

    @staticmethod
    def format_time(seconds):
        """
        Formatuje czas w sekundach na format MM:SS.
        """
        minutes, sec = divmod(int(seconds), 60)
        return f"{minutes:02d}:{sec:02d}"

    def show_message(self, message):
        """
        Wyświetla wiadomość informacyjną.
        """
        messagebox.showinfo("Informacja", message)

    def reset_ui(self):
        """
        Resetuje interfejs użytkownika po zakończeniu procesu.
        """
        self.is_running = False
        self.start_button.configure(state="normal")

def main():
    app = FeedPetsApp()
    app.mainloop()

if __name__ == "__main__":
    main()
