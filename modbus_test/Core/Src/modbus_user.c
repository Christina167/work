#include "mb.h"
#include "mbport.h"

#define REG_HOLDING_START   1
#define REG_HOLDING_NREGS   16

static USHORT usHoldingBuf[REG_HOLDING_NREGS] =
{
    100, 200, 300, 400,
    500, 600, 700, 800,
    0, 0, 0, 0,
    0, 0, 0, 0
};

eMBErrorCode eMBRegHoldingCB(UCHAR *pucRegBuffer,
                             USHORT usAddress,
                             USHORT usNRegs,
                             eMBRegisterMode eMode)
{
    USHORT iRegIndex;

    if ((usAddress >= REG_HOLDING_START) &&
        (usAddress + usNRegs <= REG_HOLDING_START + REG_HOLDING_NREGS))
    {
        iRegIndex = usAddress - REG_HOLDING_START;

        while (usNRegs > 0)
        {
            if (eMode == MB_REG_READ)
            {
                *pucRegBuffer++ = (UCHAR)(usHoldingBuf[iRegIndex] >> 8);
                *pucRegBuffer++ = (UCHAR)(usHoldingBuf[iRegIndex] & 0xFF);
            }
            else
            {
                usHoldingBuf[iRegIndex] = (USHORT)(*pucRegBuffer++ << 8);
                usHoldingBuf[iRegIndex] |= (USHORT)(*pucRegBuffer++);
            }

            iRegIndex++;
            usNRegs--;
        }

        return MB_ENOERR;
    }

    return MB_ENOREG;
}

eMBErrorCode eMBRegInputCB(UCHAR *pucRegBuffer,
                           USHORT usAddress,
                           USHORT usNRegs)
{
    (void)pucRegBuffer;
    (void)usAddress;
    (void)usNRegs;

    return MB_ENOREG;
}

eMBErrorCode eMBRegCoilsCB(UCHAR *pucRegBuffer,
                           USHORT usAddress,
                           USHORT usNCoils,
                           eMBRegisterMode eMode)
{
    (void)pucRegBuffer;
    (void)usAddress;
    (void)usNCoils;
    (void)eMode;

    return MB_ENOREG;
}

eMBErrorCode eMBRegDiscreteCB(UCHAR *pucRegBuffer,
                              USHORT usAddress,
                              USHORT usNDiscrete)
{
    (void)pucRegBuffer;
    (void)usAddress;
    (void)usNDiscrete;

    return MB_ENOREG;
}
