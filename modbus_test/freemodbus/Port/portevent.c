#include "port.h"
#include "mb.h"
#include "mbport.h"

static eMBEventType eQueuedEvent;
static BOOL xEventInQueue;

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
    if (xEventInQueue)
    {
        *eEvent = eQueuedEvent;
        xEventInQueue = FALSE;
        return TRUE;
    }

    return FALSE;
}