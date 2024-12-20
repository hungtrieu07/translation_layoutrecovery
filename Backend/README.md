# Instruction

1. Put the private files into Backend/ folder (.env and .json files)

2. Create a new virtual environment with Python and activate it.

    ```bash
    cd Backend/
    python -m venv env
    source env/bin/activate
    ```

    **Suggestion**: Using `conda` with `python=3.8` if you don't want to use Python venv.

    ```bash
    conda create -n backend python=3.8
    conda activate backend
    ```

3. Install the dependencies.

    ```bash
    pip install -r requirements.txt
    ```

4. Run the application (make sure you have PostgreSQL running on your machine and please change the database settings in settings.py to your own database settings...)

    - If you have `bash/zsh/sh/...` shells, just replace with the `<shell>` that you have. For example, you have `bash`:

        ```bash
        bash setup.sh
        ```

    - Else:

        ```bash
        gdown --id 1Jx2m_2I1d9PYzFRQ4gl82xQa-G7Vsnsl
        python -m pip install -e ../.
        python manage.py makemigrations account translation 
        python manage.py migrate 
        python manage.py runserver 0.0.0.0:8000
        ```

5. *(Optional - Should use when running on a server)* Deploy Backend on **ngrok**

    - Open a new terminal and run **ngrok**:

        ```bash
        ngrok http 8000
        ```

    - If not using **ngrok**, just use the localhost [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
