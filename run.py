from os import environ

from mealieshopper import create_app

app = create_app()

if __name__ == "__main__":
    # Zonder reloader, zoals Flask in 3.x: Quart start er standaard een, en die herstart de app bij elke wijziging.
    app.run(host="0.0.0.0", port=int(environ.get("PORT", "8000")), use_reloader=False)
