from os import environ

from mealieshopper import create_app, offers_schedule

app = create_app()
# De dagelijkse aanbiedingen-run draait in een achtergrondthread van de server. Niet in create_app: de tests
# maken de app vaak aan en horen geen threads te starten.
if not environ.get("MEALIESHOPPER_TESTING"):
    offers_schedule.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(environ.get("PORT", "8000")))
