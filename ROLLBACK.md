# Rollback

El original permanece intacto. Detenga el nuevo servicio, restaure DNS/LB y vuelva a iniciar el despliegue original. No ejecute dos NOC con polling/scheduler simultáneo. Los datos generados tras el corte deben exportarse antes del rollback.
