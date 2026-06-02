from os import environ

from mealieshopper import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(environ.get("PORT", "8000")))
