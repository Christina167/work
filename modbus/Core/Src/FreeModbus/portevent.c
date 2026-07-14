#include "port.h"
#include "mb.h"
#include "mbport.h"

static volatile eMBEventType eQueuedEvent;
static volatile BOOL xEventInQueue = FALSE;

BOOL xMBPortEventInit(void)
{
    xEventInQueue = FALSE;
    return TRUE;
}

BOOL xMBPortEventPost(eMBEventType eEvent)
{
    eQueuedEvent = eEvent;
    xEventInQueue = TRUE;

    return TRUE;
}

BOOL xMBPortEventGet(eMBEventType *eEvent)
{
    BOOL xEventAvailable = FALSE;

    ENTER_CRITICAL_SECTION();

    if (xEventInQueue == TRUE)
    {
        *eEvent = eQueuedEvent;
        xEventInQueue = FALSE;
        xEventAvailable = TRUE;
    }

    EXIT_CRITICAL_SECTION();

    return xEventAvailable;
}
