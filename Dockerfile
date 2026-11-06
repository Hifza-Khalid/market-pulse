# Use the exact Airflow version you are using
FROM apache/airflow:2.10.5

# Switch to root to install system packages if needed (optional)
# USER root

# Copy the requirements file into the image
COPY requirements.txt .

# Install the Python packages as the airflow user
USER airflow
RUN pip install --no-cache-dir -r requirements.txt